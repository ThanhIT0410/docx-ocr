// Detects an NVIDIA GPU via `nvidia-smi` (installed by the NVIDIA driver,
// not something this app ships) — used to gate the local llama.cpp
// auto-spawn (electron/llama-manager.js): the bundled llama-server.exe/
// ggml-cuda.dll build is CUDA-only (see pyinstaller.spec's sibling,
// electron-builder.yml's llama extraResources — no CPU-only fallback DLL is
// bundled, since this OCR model is far too slow on CPU to be usable), so
// there is no point downloading a 4GB model on a machine that could never
// run it.
'use strict'

const { execFile } = require('node:child_process')

/** @returns {Promise<string | null>} GPU name if an NVIDIA GPU is present, else null. */
function detectNvidiaGpu() {
  return new Promise((resolve) => {
    execFile('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader'], { timeout: 10_000 }, (err, stdout) => {
      if (err) return resolve(null) // not installed, not on PATH, or no NVIDIA device — all mean "no usable GPU" here
      const name = stdout.split('\n').map((l) => l.trim()).find(Boolean)
      resolve(name || null)
    })
  })
}

module.exports = { detectNvidiaGpu }
