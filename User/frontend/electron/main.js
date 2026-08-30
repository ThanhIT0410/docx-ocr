'use strict'

const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const path = require('node:path')
const fs = require('node:fs/promises')
const { startSidecar } = require('./sidecar')
const { startStaticServer } = require('./staticServer')

const isDev = !app.isPackaged
const DEV_RENDERER_URL = 'http://localhost:3000'

let mainWindow
let sidecarHandle
let staticServerHandle

// Single instance: two copies of this app would otherwise spawn two FastAPI
// sidecars competing for the same on-disk preview directory (§10).
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
}

async function createWindow() {
  const userDataPath = app.getPath('userData')

  const rendererUrl = isDev
    ? DEV_RENDERER_URL
    : (staticServerHandle = await startStaticServer(path.join(__dirname, '..', '.output', 'public'))).baseUrl

  sidecarHandle = await startSidecar({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    // electron/ -> frontend/ -> User/ (repo layout: User/frontend + User/backend
    // are siblings) — sidecar.js's dev branch joins 'backend' onto this.
    projectRoot: path.join(__dirname, '..', '..'),
    dataDir: userDataPath,
    allowedOrigin: rendererUrl
  })

  // Visible to preload.js via process.env (see comment there).
  process.env.OCR_SIDECAR_BASE_URL = sidecarHandle.baseUrl
  process.env.OCR_USER_DATA_PATH = userDataPath
  process.env.OCR_APP_VERSION = app.getVersion()

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#EEF1F5',
    // Only set in dev: there's no packaged .exe yet to inherit an icon
    // from, and `build/` isn't part of the packaged app's own files (see
    // electron-builder.yml's `files:`) — the packaged app instead gets its
    // icon "for free" from the .exe resource electron-builder embeds via
    // `win.icon`, which Windows uses for the taskbar/title bar on its own.
    ...(isDev ? { icon: path.join(__dirname, '..', 'build', 'icon.ico') } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false // preload needs `process.env`; keep contextIsolation as the real boundary.
    }
  })

  mainWindow.setMenuBarVisibility(false)
  await mainWindow.loadURL(rendererUrl)

  mainWindow.on('closed', () => { mainWindow = undefined })
}

ipcMain.handle('save-file', async (_event, arrayBuffer, suggestedName) => {
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, { defaultPath: suggestedName })
  if (canceled || !filePath) return { saved: false }
  await fs.writeFile(filePath, Buffer.from(arrayBuffer))
  return { saved: true, filePath }
})

function shutdownSidecar() {
  sidecarHandle?.kill()
  staticServerHandle?.close()
}

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  shutdownSidecar()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', shutdownSidecar)
// Belt-and-suspenders for abrupt termination (§10 "kể cả khi Electron bị đóng đột ngột").
process.on('exit', shutdownSidecar)

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
