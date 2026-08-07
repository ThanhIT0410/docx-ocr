'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// process.env here is inherited from the main process (same OS process),
// so values main.js sets before loadURL() are visible here without an IPC
// round trip.
contextBridge.exposeInMainWorld('processorBridge', {
  appVersion: process.env.OCR_APP_VERSION || '',
  // Set by main.js right after startBackend() resolves — the port and both
  // API keys are generated fresh every launch (see electron/sidecar.js), so
  // there's nothing to read here until the backend is actually up.
  apiBaseUrl: process.env.PROCESSOR_API_BASE_URL || '',
  apiKey: process.env.PROCESSOR_API_KEY || '',
  adminApiKey: process.env.PROCESSOR_ADMIN_API_KEY || '',
  // app/pages/settings.vue — get/save never carry a raw secret value over
  // this bridge (see electron/settings-store.js's toRendererShape), and
  // saving doesn't restart anything by itself: applying a change needs a
  // full app relaunch (backend env is only read once at process start),
  // which the settings page triggers explicitly via relaunch() after the
  // operator confirms.
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (partial) => ipcRenderer.invoke('settings:save', partial),
  relaunch: () => ipcRenderer.invoke('settings:relaunch')
})

// Same BrowserWindow, same preload — used while electron/setup.html (GPU
// check / model download / llama.cpp+backend startup progress) is loaded,
// before main.js switches the window over to the real app URL.
contextBridge.exposeInMainWorld('processorSetup', {
  onProgress: (cb) => ipcRenderer.on('setup-progress', (_event, data) => cb(data)),
  continueWithoutLlama: () => ipcRenderer.send('setup-continue-without-llama'),
  quit: () => ipcRenderer.send('setup-quit'),
  // First-run connection setup (electron/main.js's waitForFirstRunSave) —
  // { supabaseServiceRoleKey, supabaseAccessToken }.
  saveFirstRun: (data) => ipcRenderer.send('setup-first-run-save', data)
})
