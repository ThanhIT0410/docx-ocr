'use strict'

const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const { startBackend } = require('./sidecar')
const { startStaticServer } = require('./staticServer')
const { detectNvidiaGpu } = require('./gpu-check')
const { ensureModelsDownloaded, modelsReady, modelPaths } = require('./llama-downloader')
const { startLlama } = require('./llama-manager')
const { readSettings, writeSettings, toRendererShape } = require('./settings-store')

const isDev = !app.isPackaged
const DEV_RENDERER_URL = 'http://localhost:3000'

let mainWindow
let backendHandle
let llamaHandle
let staticServerHandle
// True only while ensureModelsDownloaded() is actually in flight (set/reset
// around that one call in createWindow()) — gates the close-confirmation
// dialog below so it only interrupts an in-progress multi-GB transfer, not
// every other phase of startup where there's nothing meaningful to lose.
let isDownloadingModel = false

// Single instance: two copies of this app would otherwise each spawn their
// own admin API against the same Supabase project — each with its own
// in-process queue/OCR pipeline (app/services/queue_service.py,
// ocr_pipeline.py), which only ever coordinate through this one instance's
// memory. A second instance has no way to know what the first is doing,
// so it's not just "wasteful", two of these could genuinely race on the
// same exam.
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

function resolveBackendOpts() {
  return {
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    // electron/ -> frontend/ -> Processor/ (repo layout: Processor/frontend
    // + Processor/backend are siblings) — sidecar.js's/llama-manager.js's
    // dev branches join 'backend'/'Llama' onto this.
    projectRoot: path.join(__dirname, '..', '..')
  }
}

/** Path to the admin API's own `.env` — same resolution sidecar.js's
 * resolveCommand() uses for that process's cwd. Read (never written) here
 * only to check whether the operator already pointed PROCESSOR_LLAMACPP_BASE_URL
 * at something else (a separate GPU server, say) — see comment below. */
function backendEnvPath(opts) {
  return opts.isPackaged
    ? path.join(opts.resourcesPath, 'backend', '.env')
    : path.join(opts.projectRoot, 'backend', '.env')
}

function readEnvValue(envPath, key) {
  try {
    const text = fs.readFileSync(envPath, 'utf8')
    const line = text.split(/\r?\n/).find((l) => l.trim().startsWith(`${key}=`))
    return line ? line.slice(line.indexOf('=') + 1).trim() : ''
  } catch {
    return ''
  }
}

/** Resolves once the user clicks "Tiếp tục" on the setup screen's
 * gpu-missing/recoverable-error state. */
function waitForContinue() {
  return new Promise((resolve) => {
    ipcMain.once('setup-continue-without-llama', resolve)
  })
}

/** Resolves once the first-run phase's form is submitted (see
 * electron/setup.html's 'first-run' case) — only reached when
 * readSettings() is still missing supabaseUrl or supabaseServiceRoleKey,
 * so this is a one-time gate per install, not shown again once both are set. */
function waitForFirstRunSave() {
  return new Promise((resolve) => {
    ipcMain.once('setup-first-run-save', (_event, data) => {
      writeSettings({
        supabaseUrl: data.supabaseUrl,
        supabaseServiceRoleKey: data.supabaseServiceRoleKey,
        supabaseAccessToken: data.supabaseAccessToken || ''
      })
      resolve()
    })
  })
}

ipcMain.on('setup-quit', () => app.quit())

// Settings page (app/pages/settings.vue) — get/save never hand the
// renderer a raw secret value (see settings-store.js's toRendererShape),
// and saving never restarts anything itself: applying a change needs the
// whole app relaunched (env vars are read once at backend process start),
// which the renderer explicitly triggers via 'settings:relaunch' after
// confirming with the operator. The renderer is responsible for blocking
// this while OCR is running (it already polls pipeline state for the
// exams page) — a single trusted local renderer, no reason to duplicate
// that check here too.
ipcMain.handle('settings:get', () => toRendererShape(readSettings()))
ipcMain.handle('settings:save', (_event, partial) => {
  writeSettings(partial)
  return true
})
ipcMain.handle('settings:relaunch', () => {
  // app.exit() (unlike app.quit()) skips 'before-quit'/'window-all-closed'
  // entirely — without this explicit shutdown() call first, the spawned
  // backend/llama.cpp child processes would be orphaned (still running,
  // now unreachable) every time this fires, one more pair leaking per
  // Settings save.
  shutdown()
  app.relaunch()
  app.exit(0)
})

async function createWindow() {
  const rendererUrl = isDev
    ? DEV_RENDERER_URL
    : (staticServerHandle = await startStaticServer(path.join(__dirname, '..', '.output', 'public'))).baseUrl

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#EEF1F5',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false // preload needs `process.env`; keep contextIsolation as the real boundary.
    }
  })
  mainWindow.setMenuBarVisibility(false)

  // Downloading the OCR model (electron/llama-downloader.js) takes minutes
  // and is resumable (writes to a .part file, only renamed to the final
  // name once fully verified) — closing mid-download is always *safe* in
  // the sense that nothing gets corrupted, but silently losing an
  // in-progress multi-GB transfer to a stray Alt+F4/misclick is still worth
  // confirming rather than doing without asking. `mainWindow.destroy()`
  // (not another `close()`) on confirmation bypasses this same handler
  // instead of re-triggering it.
  mainWindow.on('close', (event) => {
    if (!isDownloadingModel) return
    event.preventDefault()
    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'question',
      buttons: ['Đóng', 'Huỷ'],
      defaultId: 1,
      cancelId: 1,
      title: 'Đang tải model OCR',
      message: 'Model OCR chưa tải xong. Đóng bây giờ vẫn an toàn — lần mở sau sẽ tải tiếp từ chỗ dở dang, không mất tiến trình đã tải, nhưng sẽ phải đợi lại.\n\nBạn có chắc muốn đóng không?'
    })
    if (choice === 0) mainWindow.destroy()
  })

  // Setup phase: (maybe) first-run connection setup -> GPU check -> (maybe)
  // download the OCR model -> spawn llama.cpp -> spawn admin API — shown
  // as progress on electron/setup.html before switching this same window
  // over to the real app. See that file + electron/llama-manager.js/
  // llama-downloader.js/settings-store.js.
  await mainWindow.loadFile(path.join(__dirname, 'setup.html'))
  const sendProgress = (data) => mainWindow?.webContents.send('setup-progress', data)

  // Connection setup comes first, logically — nothing else here can
  // succeed without a Supabase URL + service_role key
  // (app/config/settings.py requires both, no default for either — a
  // backend spawned without one exits immediately, which is exactly the
  // "Admin API đã thoát trước khi sẵn sàng" failure sidecar.js's
  // waitForHealth() surfaces if this gate is skipped). Only asked once:
  // skipped entirely once settings-store.js has both on file
  // (settings.vue's Supabase URL field can still be a blank
  // DEFAULT_SUPABASE_URL for a customer-specific build — see
  // settings-store.js — in which case this gate never even fires).
  {
    const current = readSettings()
    if (!current.supabaseUrl || !current.supabaseServiceRoleKey) {
      sendProgress({ phase: 'first-run' })
      await waitForFirstRunSave()
    }
  }

  const opts = resolveBackendOpts()
  const envPath = backendEnvPath(opts)
  // If the operator's own .env already names a llama.cpp instance (e.g. a
  // separate GPU server, per the original architecture — see
  // backend_requirements.md), respect that and skip GPU-check/download/
  // local-spawn entirely rather than silently overriding an explicit choice.
  const llamaAlreadyConfigured = !!readEnvValue(envPath, 'PROCESSOR_LLAMACPP_BASE_URL')

  if (!llamaAlreadyConfigured) {
    sendProgress({ phase: 'gpu-check' })
    const gpuName = await detectNvidiaGpu()
    if (!gpuName) {
      sendProgress({ phase: 'gpu-missing' })
      await waitForContinue()
    } else {
      try {
        const modelsDir = path.join(app.getPath('userData'), 'llama-models')
        if (!modelsReady(modelsDir)) {
          isDownloadingModel = true
          try {
            await ensureModelsDownloaded(modelsDir, (p) => sendProgress({ phase: 'downloading', ...p }))
          } finally {
            isDownloadingModel = false
          }
        }
        sendProgress({ phase: 'starting-llama' })
        const paths = modelPaths(modelsDir)
        llamaHandle = await startLlama({ ...opts, modelPath: paths.model, mmprojPath: paths.mmproj })
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        sendProgress({
          phase: 'gpu-missing',
          message: `Không khởi động được OCR engine: ${message}\nCó thể tiếp tục nhưng chức năng OCR sẽ không hoạt động.`
        })
        await waitForContinue()
      }
    }
  }

  // PROCESSOR_LLAMACPP_BASE_URL/_MODEL are required (no default) in the
  // backend's own Settings schema — if auto-mode was attempted and failed
  // (GPU missing, or startLlama() threw) and the operator chose "Tiếp tục
  // không có OCR", `llamaHandle` is still undefined here, and skipping the
  // override entirely used to leave those two fields with no value *at
  // all* (not just non-functional) whenever the operator's own .env didn't
  // set them either (true by design once GPU-auto-mode is enabled — see
  // llamaAlreadyConfigured above) — Settings() then failed pydantic
  // validation and the whole Admin API process exited immediately, which
  // is a real bug reported in testing (both "ECONNRESET" *and* "Admin API
  // đã thoát trước khi sẵn sàng" firing together). A closed local port is
  // a valid but non-functional URL: check_llamacpp_health() (health_check.py)
  // already handles connection failures by returning False, so the rest of
  // the app degrades exactly as intended instead of refusing to start.
  let llamaEnv
  if (llamaHandle) {
    llamaEnv = { PROCESSOR_LLAMACPP_BASE_URL: `${llamaHandle.baseUrl}/v1`, PROCESSOR_LLAMACPP_MODEL: llamaHandle.alias }
  } else if (!llamaAlreadyConfigured) {
    llamaEnv = { PROCESSOR_LLAMACPP_BASE_URL: 'http://127.0.0.1:1/v1', PROCESSOR_LLAMACPP_MODEL: 'unavailable' }
  }

  sendProgress({ phase: 'starting-backend' })
  try {
    backendHandle = await startBackend({ ...opts, extraEnv: llamaEnv })
  } catch (err) {
    sendProgress({ phase: 'error', message: err instanceof Error ? err.message : String(err) })
    return
  }

  sendProgress({ phase: 'ready' })

  // Visible to preload.js via process.env (see comment there).
  process.env.OCR_APP_VERSION = app.getVersion()
  process.env.PROCESSOR_API_BASE_URL = backendHandle.baseUrl
  process.env.PROCESSOR_API_KEY = backendHandle.apiKey
  process.env.PROCESSOR_ADMIN_API_KEY = backendHandle.adminApiKey

  await mainWindow.loadURL(rendererUrl)
  mainWindow.on('closed', () => { mainWindow = undefined })
}

function shutdown() {
  backendHandle?.kill()
  llamaHandle?.kill()
  staticServerHandle?.close()
}

app.whenReady().then(() => {
  createWindow().catch((err) => {
    dialog.showErrorBox('Không thể khởi động Processor', err instanceof Error ? err.message : String(err))
    app.quit()
  })
})

app.on('window-all-closed', () => {
  shutdown()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', shutdown)
// Belt-and-suspenders for abrupt termination, same as User/frontend.
process.on('exit', shutdown)

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
