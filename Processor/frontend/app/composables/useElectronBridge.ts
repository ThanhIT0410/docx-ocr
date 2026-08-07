import type { ProcessorSettings, ProcessorSettingsUpdate } from '~/types/models'

export interface ProcessorBridge {
  appVersion: string
  /** Base URL of the locally-spawned admin API, e.g. http://127.0.0.1:53214 —
   * empty until electron/main.js's startBackend() resolves. */
  apiBaseUrl: string
  /** Generated fresh every launch (electron/sidecar.js) — never persisted,
   * never baked into the installer. */
  apiKey: string
  adminApiKey: string
  /** app/pages/settings.vue — never carries a raw secret value (see
   * electron/settings-store.js's toRendererShape). */
  getSettings: () => Promise<ProcessorSettings>
  /** Applying a change requires relaunch() — the backend only reads its
   * env once at process start. */
  saveSettings: (partial: ProcessorSettingsUpdate) => Promise<boolean>
  relaunch: () => Promise<void>
}

declare global {
  interface Window {
    /** Exposed by electron/preload.js via contextBridge. Undefined when the
     * app runs as a plain browser tab (`nuxt dev` without Electron). */
    processorBridge?: ProcessorBridge
  }
}

/** True when running inside the packaged/dev Electron shell. */
export function useIsElectron(): boolean {
  return typeof window !== 'undefined' && !!window.processorBridge
}

export function useElectronBridge(): ProcessorBridge | undefined {
  return typeof window !== 'undefined' ? window.processorBridge : undefined
}
