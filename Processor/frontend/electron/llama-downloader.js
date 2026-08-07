// Downloads the OCR model + multimodal projector from HuggingFace on first
// run (electron/llama-manager.js calls this before spawning llama-server.exe)
// — these are ~3.5GB combined, far too large to bundle into the installer
// (see electron-builder.yml's llama extraResources, which only ships the
// llama.cpp binaries/DLLs, not weights). Supports resuming a partial
// download via HTTP Range requests: at 3.5GB, restarting from scratch after
// any interruption (closed app, flaky connection) would be a bad first-run
// experience.
'use strict'

const http = require('node:http')
const https = require('node:https')
const fs = require('node:fs')
const path = require('node:path')

// prithivMLmods/chandra-ocr-2-GGUF on HuggingFace — verified reachable and
// serving these exact files (`accept-ranges: bytes`) before wiring this up.
const MODEL_FILES = [
  {
    name: 'chandra-ocr-2.Q4_K_M.gguf',
    url: 'https://huggingface.co/prithivMLmods/chandra-ocr-2-GGUF/resolve/main/chandra-ocr-2.Q4_K_M.gguf'
  },
  {
    name: 'chandra-ocr-2.mmproj-f16.gguf',
    url: 'https://huggingface.co/prithivMLmods/chandra-ocr-2-GGUF/resolve/main/chandra-ocr-2.mmproj-f16.gguf'
  }
]

function get(url, headers, redirectsLeft = 5) {
  // HuggingFace's own URLs are always https, but the redirect it hands back
  // (or a self-hosted alternative, if MODEL_FILES is ever repointed) isn't
  // guaranteed to be — pick the client by the URL's actual scheme instead of
  // assuming https everywhere.
  const client = url.startsWith('http://') ? http : https
  return new Promise((resolve, reject) => {
    client.get(url, { headers }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
        res.resume()
        if (redirectsLeft <= 0) return reject(new Error('Too many redirects'))
        return resolve(get(res.headers.location, headers, redirectsLeft - 1))
      }
      resolve(res)
    }).on('error', reject)
  })
}

/**
 * @param {{name: string, url: string}} file
 * @param {string} destDir
 * @param {(progress: {name: string, receivedBytes: number, totalBytes: number}) => void} onProgress
 */
async function downloadOne(file, destDir, onProgress) {
  const dest = path.join(destDir, file.name)
  const tmp = `${dest}.part`

  let existingBytes = 0
  try {
    existingBytes = fs.statSync(tmp).size
  } catch {
    existingBytes = 0
  }

  const res = await get(file.url, existingBytes > 0 ? { Range: `bytes=${existingBytes}-` } : {})

  if (res.statusCode !== 200 && res.statusCode !== 206) {
    res.resume()
    throw new Error(`HTTP ${res.statusCode} downloading ${file.name}`)
  }

  let totalBytes
  let startAt
  if (res.statusCode === 206) {
    // "bytes <start>-<end>/<total>"
    const match = /\/(\d+)$/.exec(res.headers['content-range'] || '')
    totalBytes = match ? Number(match[1]) : existingBytes + Number(res.headers['content-length'] || 0)
    startAt = existingBytes
  } else {
    // Server ignored the Range header (200, not 206) — restart this file
    // from scratch rather than silently appending onto content that may not
    // align with what's already on disk.
    totalBytes = Number(res.headers['content-length'] || 0)
    startAt = 0
  }

  await new Promise((resolve, reject) => {
    const out = fs.createWriteStream(tmp, { flags: startAt > 0 ? 'a' : 'w' })
    let received = startAt
    res.on('data', (chunk) => {
      received += chunk.length
      onProgress({ name: file.name, receivedBytes: received, totalBytes })
    })
    res.on('error', reject)
    out.on('error', reject)
    out.on('finish', resolve)
    res.pipe(out)
  })

  const finalSize = fs.statSync(tmp).size
  if (totalBytes && finalSize !== totalBytes) {
    throw new Error(`${file.name}: kích thước sau khi tải (${finalSize}) không khớp kỳ vọng (${totalBytes})`)
  }
  fs.renameSync(tmp, dest)
}

/**
 * @param {string} destDir
 * @returns {boolean} true if both model files already exist (nothing to download).
 */
function modelsReady(destDir) {
  return MODEL_FILES.every((f) => fs.existsSync(path.join(destDir, f.name)))
}

// A multi-GB download over consumer/corporate networks hitting a transient
// drop (ECONNRESET etc.) at least once isn't the exceptional case, it's the
// expected one — retrying automatically (resuming via the .part file, not
// restarting) means one hiccup doesn't force the operator to notice, close,
// and reopen the app just to continue where it already was.
const MAX_DOWNLOAD_RETRIES = 5
const RETRY_BASE_DELAY_MS = 2000

async function downloadOneWithRetry(file, destDir, onProgress) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await downloadOne(file, destDir, onProgress)
    } catch (err) {
      if (attempt >= MAX_DOWNLOAD_RETRIES) throw err
      const delayMs = RETRY_BASE_DELAY_MS * 2 ** attempt
      // eslint-disable-next-line no-console
      console.warn(`[llama-downloader] ${file.name} failed (${err.message}), retrying in ${delayMs}ms (attempt ${attempt + 1}/${MAX_DOWNLOAD_RETRIES})`)
      await new Promise((resolve) => setTimeout(resolve, delayMs))
    }
  }
}

/**
 * @param {string} destDir
 * @param {(progress: {name: string, fileIndex: number, fileCount: number, receivedBytes: number, totalBytes: number}) => void} onProgress
 */
async function ensureModelsDownloaded(destDir, onProgress) {
  fs.mkdirSync(destDir, { recursive: true })
  for (let i = 0; i < MODEL_FILES.length; i++) {
    const file = MODEL_FILES[i]
    if (fs.existsSync(path.join(destDir, file.name))) continue
    await downloadOneWithRetry(file, destDir, (p) =>
      onProgress({ ...p, fileIndex: i, fileCount: MODEL_FILES.length }))
  }
}

function modelPaths(destDir) {
  return {
    model: path.join(destDir, MODEL_FILES[0].name),
    mmproj: path.join(destDir, MODEL_FILES[1].name)
  }
}

module.exports = { ensureModelsDownloaded, modelsReady, modelPaths, downloadOne, MODEL_FILES }
