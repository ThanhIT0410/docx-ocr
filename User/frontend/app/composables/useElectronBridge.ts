export interface OcrBridge {
  /** Base URL of the local FastAPI sidecar, e.g. http://127.0.0.1:53214 */
  sidecarBaseUrl: string
  /** Per-OS app data directory, passed through so the renderer can display it
   * (e.g. in an error message) — actual disk access always goes through the
   * sidecar, never directly from the renderer. */
  userDataPath: string
  appVersion: string
}

declare global {
  interface Window {
    /** Exposed by electron/preload.js via contextBridge. Undefined when the
     * app runs as a plain browser tab (`nuxt dev` without Electron). */
    ocrBridge?: OcrBridge
  }
}

/** True when running inside the packaged/dev Electron shell. */
export function useIsElectron(): boolean {
  return typeof window !== 'undefined' && !!window.ocrBridge
}

export function useElectronBridge(): OcrBridge | undefined {
  return typeof window !== 'undefined' ? window.ocrBridge : undefined
}
