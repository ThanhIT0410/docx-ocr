'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// process.env here is inherited from the main process (same OS process),
// so values main.js sets before loadURL() are visible here without an IPC
// round trip. See main.js for where these are assigned.
contextBridge.exposeInMainWorld('ocrBridge', {
  sidecarBaseUrl: process.env.OCR_SIDECAR_BASE_URL || '',
  userDataPath: process.env.OCR_USER_DATA_PATH || '',
  appVersion: process.env.OCR_APP_VERSION || '',

  /** Opens a native "Save As" dialog and writes the given bytes to disk.
   * Used by the Finished view's "Xuất kết quả" button. */
  saveFile: (arrayBuffer, suggestedName) => ipcRenderer.invoke('save-file', arrayBuffer, suggestedName)
})
