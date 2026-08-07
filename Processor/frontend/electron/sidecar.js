// Owns the lifecycle of the local admin API process — runs from
// Processor/backend/dist/ocr-processor-backend[.exe] in prod (see
// run.py/pyinstaller.spec), or `run.py api` through the project venv in
// dev. Mirrors what User/frontend/electron/sidecar.js does for its own
// FastAPI sidecar. Only one child process now — OCR itself runs as a
// background task inside this same api process (started via
// POST /processor/ocr/start, see app/services/ocr_pipeline.py), not a
// separate worker process like an earlier version of this backend had.
//
// Secrets are split two ways:
//  - Supabase/llama.cpp credentials (PROCESSOR_SUPABASE_*, PROCESSOR_LLAMACPP_*)
//    are real operator-owned secrets this process has no business inventing
//    — they come from a `.env` file placed next to the backend
//    (pydantic-settings reads it automatically via `cwd`), exactly like
//    running `python -m app.main` by hand today (see .env.example). Never
//    baked into the installer.
//  - The admin API's own auth keys (PROCESSOR_API_KEY/PROCESSOR_ADMIN_API_KEY)
//    are generated fresh every launch instead: nothing outside this process
//    ever needs to know them ahead of time (the console is the only caller,
//    and gets them at runtime via preload.js), so there's no more
//    "keep two .env files in sync" step for just these two.
'use strict'

const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const net = require('node:net')
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')
const { readSettings, toEnvVars } = require('./settings-store')

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

function waitForHealth(baseUrl, timeoutMs, isAlive) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (!isAlive()) {
        return reject(new Error(
          'Tiến trình Admin API đã thoát trước khi sẵn sàng — kiểm tra file .env ' +
          '(xem .env.example) trong thư mục backend.'
        ))
      }
      const req = http.get(`${baseUrl}/health`, (res) => {
        res.resume()
        if (res.statusCode === 200) return resolve()
        retry()
      })
      req.on('error', retry)
      req.setTimeout(1000, () => req.destroy())
    }
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error('Admin API health check timed out'))
      setTimeout(tick, 300)
    }
    tick()
  })
}

/** @returns {{ command: string, baseArgs: string[], cwd: string }} */
function resolveCommand(opts) {
  if (opts.isPackaged) {
    // Built by PyInstaller — see backend/pyinstaller.spec. electron-builder
    // copies the output into resources/backend/ (extraResources).
    const exeName = process.platform === 'win32' ? 'ocr-processor-backend.exe' : 'ocr-processor-backend'
    const command = path.join(opts.resourcesPath, 'backend', exeName)
    if (!fs.existsSync(command)) {
      throw new Error(`Không tìm thấy tiến trình nền tại ${command}. Gói cài đặt có thể bị lỗi.`)
    }
    return { command, baseArgs: [], cwd: path.dirname(command) }
  }
  // Dev mode: run run.py's own dispatch through the project's venv — same
  // code path as prod instead of a separate `python -m app.main` branch,
  // so dev and packaged behavior can't quietly diverge.
  const venvPython = process.platform === 'win32'
    ? path.join(opts.projectRoot, 'backend', '.venv', 'Scripts', 'python.exe')
    : path.join(opts.projectRoot, 'backend', '.venv', 'bin', 'python')
  const command = fs.existsSync(venvPython) ? venvPython : 'python'
  return { command, baseArgs: ['run.py'], cwd: path.join(opts.projectRoot, 'backend') }
}

function spawnChild(label, command, args, cwd, env) {
  const child = spawn(command, args, { cwd, stdio: 'pipe', env })
  child.stdout.on('data', (d) => process.stdout.write(`[${label}] ${d}`))
  child.stderr.on('data', (d) => process.stderr.write(`[${label}:err] ${d}`))

  let killedIntentionally = false
  child.on('exit', (code, signal) => {
    if (!killedIntentionally && code !== 0 && code !== null) {
      // eslint-disable-next-line no-console
      console.error(`[${label}] exited unexpectedly (code=${code}, signal=${signal})`)
    }
  })

  function kill() {
    if (child.exitCode !== null) return
    killedIntentionally = true
    if (process.platform === 'win32') {
      // Ensures the whole process tree dies on Windows.
      spawn('taskkill', ['/pid', String(child.pid), '/f', '/t'])
    } else {
      child.kill('SIGTERM')
    }
  }

  return { kill, isAlive: () => child.exitCode === null }
}

/**
 * @param {object} opts
 * @param {boolean} opts.isPackaged
 * @param {string} opts.resourcesPath  process.resourcesPath in prod
 * @param {string} opts.projectRoot    Processor/ repo root in dev (backend/
 *                                     and frontend/ are siblings)
 * @param {Record<string,string>} [opts.extraEnv] extra vars merged into the
 *   spawned process's env (currently: llama.cpp base URL/model, when
 *   main.js auto-spawned it — see electron/llama-manager.js)
 */
async function startBackend(opts) {
  const port = await findFreePort()
  const baseUrl = `http://127.0.0.1:${port}`
  const apiKey = crypto.randomBytes(24).toString('hex')
  const adminApiKey = crypto.randomBytes(24).toString('hex')

  const { command, baseArgs, cwd } = resolveCommand(opts)

  // PYTHONPATH cleared for the same reason as User/frontend/electron/sidecar.js:
  // a dev machine's global PYTHONPATH can shadow this venv's own packages.
  // PROCESSOR_API_HOST is pinned to loopback regardless of what .env says —
  // this instance is spawned for this one desktop app, never meant to be
  // reachable off-machine (backend_requirements.md §3.7).
  //
  // Layering, deliberately in this order: extraEnv (auto-detected
  // llama.cpp, if main.js spawned it) first, then the operator's own
  // Settings-page choices (settings-store.js — e.g. an explicit llama.cpp
  // model override) so they can override the auto-detected value, then
  // finally the truly fixed block (host/port/generated API keys) which
  // must always win regardless of what's in the settings file.
  const sharedEnv = {
    ...process.env,
    PYTHONPATH: '',
    ...(opts.extraEnv || {}),
    ...toEnvVars(readSettings()),
    PROCESSOR_API_HOST: '127.0.0.1',
    PROCESSOR_API_PORT: String(port),
    PROCESSOR_API_KEY: apiKey,
    PROCESSOR_ADMIN_API_KEY: adminApiKey
  }

  const api = spawnChild('processor-api', command, baseArgs, cwd, sharedEnv)

  try {
    await waitForHealth(baseUrl, 30_000, api.isAlive)
  } catch (err) {
    api.kill()
    throw err
  }

  function kill() {
    api.kill()
  }

  return { baseUrl, apiKey, adminApiKey, kill }
}

module.exports = { startBackend, findFreePort }
