// Owns the lifecycle of the locally-spawned llama-server.exe (the OCR
// model) — mirrors electron/sidecar.js's job for the admin API, kept
// as its own module because the health-check target and readiness signal
// differ (llama.cpp's own HTTP surface, not this project's FastAPI).
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

// Model load (weights + first CUDA kernel compilation) takes noticeably
// longer than the admin API's near-instant startup — seen ~5-6s for the
// Q4_K_M model in testing, budget generously beyond that.
function waitForReady(baseUrl, timeoutMs, isAlive) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (!isAlive()) return reject(new Error('llama-server.exe đã thoát trước khi model tải xong.'))
      const req = http.get(`${baseUrl}/v1/models`, (res) => {
        res.resume()
        if (res.statusCode === 200) return resolve()
        retry()
      })
      req.on('error', retry)
      req.setTimeout(1000, () => req.destroy())
    }
    const retry = () => {
      if (Date.now() > deadline) return reject(new Error('llama-server.exe health check timed out'))
      setTimeout(tick, 500)
    }
    tick()
  })
}

function resolveLlamaServerPath(opts) {
  if (opts.isPackaged) {
    // Curated subset bundled by electron-builder.yml's llama extraResources
    // — llama-server.exe + the CUDA/multimodal DLLs it needs, NOT the full
    // Processor/Llama/ toolkit (CLI tools, every CPU-microarch variant) —
    // see that file's comment for the exact list and why.
    const exe = path.join(opts.resourcesPath, 'llama', 'llama-server.exe')
    if (!fs.existsSync(exe)) {
      throw new Error(`Không tìm thấy llama-server.exe tại ${exe}. Gói cài đặt có thể bị lỗi.`)
    }
    return exe
  }
  const exe = path.join(opts.projectRoot, 'Llama', 'llama-server.exe')
  if (!fs.existsSync(exe)) {
    throw new Error(`Không tìm thấy llama-server.exe tại ${exe} (Processor/Llama/).`)
  }
  return exe
}

/**
 * @param {object} opts
 * @param {boolean} opts.isPackaged
 * @param {string} opts.resourcesPath
 * @param {string} opts.projectRoot  Processor/ repo root in dev
 * @param {string} opts.modelPath    absolute path to the .gguf weights
 * @param {string} opts.mmprojPath   absolute path to the mmproj .gguf
 * @param {string} [opts.alias]      model id string sent in API responses
 */
async function startLlama(opts) {
  const port = await findFreePort()
  const baseUrl = `http://127.0.0.1:${port}`
  const alias = opts.alias || 'chandra-ocr-2'
  const exe = resolveLlamaServerPath(opts)

  const args = [
    '-m', opts.modelPath,
    '--mmproj', opts.mmprojPath,
    '--port', String(port),
    '--host', '127.0.0.1',
    '--ctx-size', '32768',
    '--parallel', '8',
    '--n-gpu-layers', '999',
    '--flash-attn', 'on',
    '--cache-type-k', 'q8_0',
    '--cache-type-v', 'q8_0',
    '--batch-size', '4096',
    '-a', alias
  ];

  const child = spawn(exe, args, { cwd: path.dirname(exe), stdio: 'pipe' })
  child.stdout.on('data', (d) => process.stdout.write(`[llama] ${d}`))
  child.stderr.on('data', (d) => process.stderr.write(`[llama:err] ${d}`))

  let killedIntentionally = false
  child.on('exit', (code, signal) => {
    if (!killedIntentionally && code !== 0 && code !== null) {
      // eslint-disable-next-line no-console
      console.error(`[llama] exited unexpectedly (code=${code}, signal=${signal})`)
    }
  })

  function kill() {
    if (child.exitCode !== null) return
    killedIntentionally = true
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(child.pid), '/f', '/t'])
    } else {
      child.kill('SIGTERM')
    }
  }

  try {
    await waitForReady(baseUrl, 120_000, () => child.exitCode === null)
  } catch (err) {
    kill()
    throw err
  }

  return { baseUrl, alias, kill }
}

module.exports = { startLlama }
