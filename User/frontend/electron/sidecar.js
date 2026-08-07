// Owns the lifecycle of the local FastAPI sidecar process (§9.2, §10 of
// architecture_and_requirements.md): find a free port, spawn it, wait for
// it to answer /health, and guarantee it is killed with the app — including
// on abrupt quits — so no orphaned process is left running.
'use strict'

const { spawn } = require('node:child_process')
const net = require('node:net')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')

function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

function waitForHealth(baseUrl, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(`${baseUrl}/health`, (res) => {
        res.resume()
        if (res.statusCode === 200) return resolve()
        retry()
      })
      req.on('error', retry)
      req.setTimeout(1000, () => req.destroy())
    }
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error('Sidecar health check timed out'))
      setTimeout(tick, 300)
    }
    tick()
  })
}

/**
 * @param {object} opts
 * @param {boolean} opts.isPackaged
 * @param {string} opts.resourcesPath  process.resourcesPath in prod
 * @param {string} opts.projectRoot    repo root in dev (for `python -m uvicorn`)
 * @param {string} opts.dataDir        app.getPath('userData')
 * @param {string} opts.allowedOrigin  renderer origin, for FastAPI CORS
 */
async function startSidecar(opts) {
  const port = await findFreePort()
  const baseUrl = `http://127.0.0.1:${port}`

  let command
  let commandArgs
  let cwd

  if (opts.isPackaged) {
    // Built by PyInstaller — see backend/pyinstaller.spec. electron-builder
    // copies the output into resources/backend/ (extraResources).
    const exeName = process.platform === 'win32' ? 'ocr-backend.exe' : 'ocr-backend'
    command = path.join(opts.resourcesPath, 'backend', exeName)
    commandArgs = []
    cwd = path.dirname(command)
    if (!fs.existsSync(command)) {
      throw new Error(`Không tìm thấy tiến trình nền tại ${command}. Gói cài đặt có thể bị lỗi.`)
    }
  } else {
    // Dev mode: run the FastAPI source directly through the project's venv.
    const venvPython = process.platform === 'win32'
      ? path.join(opts.projectRoot, 'backend', '.venv', 'Scripts', 'python.exe')
      : path.join(opts.projectRoot, 'backend', '.venv', 'bin', 'python')
    command = fs.existsSync(venvPython) ? venvPython : 'python'
    commandArgs = ['-m', 'app.main']
    cwd = path.join(opts.projectRoot, 'backend')
  }

  // Config goes in via env, not CLI flags — app/config.py (pydantic-settings)
  // reads OCR_* env vars directly. PYTHONPATH is explicitly cleared: a dev
  // machine's global env can point it at an unrelated project and shadow
  // this venv's packages (bit us during testing — a stray PYTHONPATH broke
  // anyio/sniffio resolution). The packaged PyInstaller binary doesn't use
  // PYTHONPATH at all, so that part only ever affects the dev-mode branch above.
  const childEnv = {
    ...process.env,
    PYTHONPATH: '',
    OCR_PORT: String(port),
    OCR_DATA_DIR: opts.dataDir,
    OCR_ALLOWED_ORIGIN: opts.allowedOrigin
  }
  const child = spawn(command, commandArgs, { cwd, stdio: 'pipe', env: childEnv })
  child.stdout.on('data', (d) => process.stdout.write(`[sidecar] ${d}`))
  child.stderr.on('data', (d) => process.stderr.write(`[sidecar:err] ${d}`))

  let killedIntentionally = false
  child.on('exit', (code, signal) => {
    if (!killedIntentionally && code !== 0 && code !== null) {
      // eslint-disable-next-line no-console
      console.error(`[sidecar] exited unexpectedly (code=${code}, signal=${signal})`)
    }
  })

  await waitForHealth(baseUrl, 30_000)

  function kill() {
    if (child.exitCode !== null) return
    killedIntentionally = true
    if (process.platform === 'win32') {
      // Ensures the whole process tree (uvicorn workers) dies on Windows.
      spawn('taskkill', ['/pid', String(child.pid), '/f', '/t'])
    } else {
      child.kill('SIGTERM')
    }
  }

  return { baseUrl, port, kill }
}

module.exports = { startSidecar, findFreePort }
