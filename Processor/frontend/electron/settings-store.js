// Manages the operator-editable connection/tuning config for the spawned
// backend (app/config/settings.py) — NOT a `.env` file the operator edits
// by hand (see Processor/DESIGN_REPORT.md's setup-friction discussion).
// Stored as plain JSON at userData/processor-settings.json, merged into
// the spawned backend's environment by sidecar.js. python-dotenv/
// pydantic-settings already treat real env vars as higher-priority than
// `.env` file contents, so this needs zero backend changes — it's purely
// an Electron-side layer that feeds env vars in before spawn.
'use strict'

const { app } = require('electron')
const fs = require('node:fs')
const path = require('node:path')

// Genuinely safe to bake a default for (see settings.vue/DESIGN_REPORT —
// unlike the two secrets below, a project URL isn't sensitive on its own).
// A deployer building a customer-specific installer edits this once;
// still shown/editable later on the Settings page regardless.
const DEFAULT_SUPABASE_URL = ''

function defaultSettings() {
  return {
    supabaseUrl: DEFAULT_SUPABASE_URL,
    supabaseServiceRoleKey: '',
    supabaseAccessToken: '',
    llamacppModel: '',
    maxPixels: null,
    workerTuning: {
      maxConcurrentPages: null,
      maxConcurrentProcessingPages: null,
      workerPollIntervalSeconds: null,
      ocrMaxAttempts: null,
      ocrBackoffBaseSeconds: null,
      ocrTemperature: null
    },
    preprocessing: {
      deskew: null,
      deskewMaxAngleDeg: null,
      enhanceContrast: null,
      contrastClipLimit: null,
      contrastTileGridSize: null
    }
  }
}

function settingsPath() {
  return path.join(app.getPath('userData'), 'processor-settings.json')
}

function readSettings() {
  try {
    const raw = fs.readFileSync(settingsPath(), 'utf8')
    const parsed = JSON.parse(raw)
    // Shallow-merge over the default shape so a settings file written by
    // an older version of this app (missing a newer field) doesn't need
    // its own migration step — the field just falls back to "not set".
    return {
      ...defaultSettings(),
      ...parsed,
      workerTuning: { ...defaultSettings().workerTuning, ...(parsed.workerTuning || {}) },
      preprocessing: { ...defaultSettings().preprocessing, ...(parsed.preprocessing || {}) }
    }
  } catch {
    return defaultSettings()
  }
}

/** `undefined` fields in `partial` keep their current value (not cleared)
 * — so saving a tuning change doesn't force re-entering the service_role
 * key every time; pass an explicit `''` to actually clear a field. */
function writeSettings(partial) {
  const current = readSettings()
  const next = {
    ...current,
    ...partial,
    workerTuning: { ...current.workerTuning, ...(partial.workerTuning || {}) },
    preprocessing: { ...current.preprocessing, ...(partial.preprocessing || {}) }
  }
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true })
  fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2), 'utf8')
  return next
}

/** Maps the JSON shape above to PROCESSOR_* env var names, 1:1 with
 * Processor/backend/.env.example's Supabase/llama.cpp-model/Worker
 * tuning/Preprocessing sections. Only emits a var for non-null/non-empty
 * fields — everything else falls through to Settings' own Python-side
 * defaults (app/config/settings.py), unchanged. */
function toEnvVars(settings) {
  const env = {}
  const put = (key, value) => {
    if (value !== null && value !== undefined && value !== '') env[key] = String(value)
  }

  put('PROCESSOR_SUPABASE_URL', settings.supabaseUrl)
  put('PROCESSOR_SUPABASE_SERVICE_ROLE_KEY', settings.supabaseServiceRoleKey)
  put('PROCESSOR_SUPABASE_ACCESS_TOKEN', settings.supabaseAccessToken)
  put('PROCESSOR_LLAMACPP_MODEL', settings.llamacppModel)
  put('PROCESSOR_MAX_PIXELS', settings.maxPixels)

  const wt = settings.workerTuning || {}
  put('PROCESSOR_MAX_CONCURRENT_PAGES', wt.maxConcurrentPages)
  put('PROCESSOR_MAX_CONCURRENT_PROCESSING_PAGES', wt.maxConcurrentProcessingPages)
  put('PROCESSOR_WORKER_POLL_INTERVAL_SECONDS', wt.workerPollIntervalSeconds)
  put('PROCESSOR_OCR_MAX_ATTEMPTS', wt.ocrMaxAttempts)
  put('PROCESSOR_OCR_BACKOFF_BASE_SECONDS', wt.ocrBackoffBaseSeconds)
  put('PROCESSOR_OCR_TEMPERATURE', wt.ocrTemperature)

  const pp = settings.preprocessing || {}
  put('PROCESSOR_PREPROCESS_DESKEW', pp.deskew)
  put('PROCESSOR_PREPROCESS_DESKEW_MAX_ANGLE_DEG', pp.deskewMaxAngleDeg)
  put('PROCESSOR_PREPROCESS_ENHANCE_CONTRAST', pp.enhanceContrast)
  put('PROCESSOR_PREPROCESS_CONTRAST_CLIP_LIMIT', pp.contrastClipLimit)
  put('PROCESSOR_PREPROCESS_CONTRAST_TILE_GRID_SIZE', pp.contrastTileGridSize)

  return env
}

/** For the renderer: never hand back the raw secret, just whether one is
 * already set (see app/pages/settings.vue's "•••• đã thiết lập" placeholder). */
function toRendererShape(settings) {
  return {
    supabaseUrl: settings.supabaseUrl,
    hasServiceRoleKey: !!settings.supabaseServiceRoleKey,
    hasAccessToken: !!settings.supabaseAccessToken,
    llamacppModel: settings.llamacppModel,
    maxPixels: settings.maxPixels,
    workerTuning: settings.workerTuning,
    preprocessing: settings.preprocessing
  }
}

module.exports = { readSettings, writeSettings, toEnvVars, toRendererShape, settingsPath }
