// Serves the statically-generated Nuxt SPA (.output/public) over
// http://127.0.0.1:<port> instead of file://. This gives the renderer a
// real HTTP origin, which is what makes Processor/backend's CORS config
// (app/main.py's `allow_origin_regex`) work — Chromium sends the opaque
// `null` origin for file:// pages, which no CORS allowlist can match.
'use strict'

const http = require('node:http')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2'
}

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

async function startStaticServer(rootDir) {
  const port = await findFreePort()
  const server = http.createServer((req, res) => {
    let reqPath = decodeURIComponent((req.url || '/').split('?')[0])
    let filePath = path.join(rootDir, reqPath)
    if (!filePath.startsWith(rootDir)) { res.writeHead(403); return res.end(); }
    if (reqPath === '/' || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
      filePath = path.join(rootDir, 'index.html') // SPA fallback for client-side routes
    }
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); return res.end('Not found'); }
      res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' })
      res.end(data)
    })
  })
  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve))
  return { baseUrl: `http://127.0.0.1:${port}`, close: () => server.close() }
}

module.exports = { startStaticServer }
