/** Mirrors Processor/backend/app/constants.py's ALL_STATUSES. */
export type ExamStatus = 'pending' | 'processing' | 'finished' | 'failed'

/** Mirrors electron/settings-store.js's `workerTuning` cluster — `null`
 * means "not customized, backend uses its own default"
 * (Processor/backend/app/config/settings.py). */
export interface WorkerTuningSettings {
  maxConcurrentPages: number | null
  /** Mirrors app/config/settings.py's max_concurrent_processing_pages —
   * total PAGES admitted into 'processing' at once, NOT a count of exams
   * (exams vary wildly in page count, see queue_service.py's module
   * docstring for why this was renamed from maxConcurrentProcessing). */
  maxConcurrentProcessingPages: number | null
  workerPollIntervalSeconds: number | null
  ocrMaxAttempts: number | null
  ocrBackoffBaseSeconds: number | null
  ocrTemperature: number | null
  ocrMaxTokens: number | null
}

/** Mirrors electron/settings-store.js's `preprocessing` cluster — same
 * "`null` = backend default" convention as WorkerTuningSettings. */
export interface PreprocessingSettings {
  deskew: boolean | null
  deskewMaxAngleDeg: number | null
  enhanceContrast: boolean | null
  contrastClipLimit: number | null
  contrastTileGridSize: number | null
}

/** What `window.processorBridge.getSettings()` resolves to — mirrors
 * electron/settings-store.js's `toRendererShape()`. Secrets are booleans
 * (`hasServiceRoleKey`/`hasAccessToken`), never the raw value — the main
 * process never hands a stored secret back to the renderer. */
export interface ProcessorSettings {
  supabaseUrl: string
  hasServiceRoleKey: boolean
  hasAccessToken: boolean
  llamacppModel: string
  /** Mirrors app/config/settings.py's max_pixels — the pixel-count cap
   * services/preprocessing.py's smart_resize enforces on every page image
   * before it's sent to the model (bounds vision token cost per request).
   * `null` = not customized, backend uses its own default (11289600). */
  maxPixels: number | null
  workerTuning: WorkerTuningSettings
  preprocessing: PreprocessingSettings
}

/** What `window.processorBridge.saveSettings()` accepts — every field
 * optional (electron/settings-store.js's `writeSettings` merges, an
 * omitted field keeps its current value; only pass
 * `supabaseServiceRoleKey`/`supabaseAccessToken` when actually changing
 * them, never just to echo the (unknown, write-only) current value). */
export interface ProcessorSettingsUpdate {
  supabaseUrl?: string
  supabaseServiceRoleKey?: string
  supabaseAccessToken?: string
  llamacppModel?: string
  maxPixels?: number | null
  workerTuning?: Partial<WorkerTuningSettings>
  preprocessing?: Partial<PreprocessingSettings>
}

/** Mirrors Processor/backend/app/schemas/dto.py::ExamListItem — GET /processor/exams.
 * No `progress`/`claimed_by` anymore — both dropped from the DB and every
 * backend response (see Processor/backend/migrations 0002 and 0003).
 * Live per-page progress now comes from GET /processor/queue/progress
 * (see ProgressResponse below), not this record. */
export interface ExamListItem {
  id: string
  title: string
  status: ExamStatus
  error_message: string | null
  uploaded_at: string
  started_at: string | null
  finished_at: string | null
}

/** One layout block within a page — mirrors
 * Processor/backend/app/schemas/models.py::DocumentLayout (same shape as
 * User/frontend/app/types/models.ts's DocumentLayout). */
export interface DocumentLayout {
  bbox: number[]
  category: string
  text: string
  level?: number | null
}

/** Mirrors Processor/backend/app/schemas/models.py::OcrPageResult — the
 * `pages.ocr_text` JSONB payload. */
export interface OcrPageResult {
  origin_width: number
  origin_height: number
  input_width: number
  input_height: number
  layouts: DocumentLayout[]
}

/** Mirrors Processor/backend/app/schemas/models.py::Page. */
export interface PageRecord {
  id: string
  exam_id: string
  page_order: number
  file_path: string
  ocr_text: OcrPageResult | null
}

/** Mirrors Processor/backend/app/schemas/dto.py::ExamDetail — GET /processor/exams/{id}. */
export interface ExamDetail extends ExamListItem {
  pages: PageRecord[]
}

/** Mirrors Processor/backend/app/schemas/dto.py::PagePreview — `url` is a
 * short-lived Supabase Storage signed URL, `null` if signing that one
 * page failed. */
export interface PagePreview {
  page_id: string
  url: string | null
}

/** Mirrors Processor/backend/app/schemas/dto.py::ExamPreviewResponse —
 * GET /processor/exams/{id}/preview. Separate from ExamDetail on purpose:
 * that endpoint is polled every few seconds while an exam processes,
 * signed URLs are not something that needs refreshing that often. */
export interface ExamPreviewResponse {
  pages: PagePreview[]
}

/** Mirrors Processor/backend/app/schemas/dto.py::EnqueueRequest/Response.
 * Batch by design — the frontend lets an operator select several exams at
 * once (see stores/exams.ts's selection state). */
export interface EnqueueItemResult {
  exam_id: string
  queued: boolean
  reason: string | null
}

export interface EnqueueResponse {
  results: EnqueueItemResult[]
  queue_size: number
  queue_max_size: number
}

/** Mirrors Processor/backend/app/schemas/dto.py::DequeueRequest/Response.
 * NOT the OCR loop's internal work-pull — this is an operator pulling
 * exam(s) back OUT of the processing queue (cancel). Reverts each to
 * 'pending' and wipes its pages' ocr_text server-side. */
export interface DequeueItemResult {
  exam_id: string
  dequeued: boolean
  reason: string | null
}

export interface DequeueResponse {
  results: DequeueItemResult[]
  queue_size: number
}

/** Mirrors Processor/backend/app/schemas/dto.py::ExamProgressItem — one
 * row of GET /processor/queue/progress, for whatever the OCR pipeline is
 * actively working on right now (in-memory on the backend, not the DB). */
export interface ExamProgressItem {
  exam_id: string
  exam_title: string
  completed_pages: number
  total_pages: number
}

/** Mirrors Processor/backend/app/schemas/dto.py::ProgressResponse.
 * `pipeline_running`/`pipeline_error` reflect the background OCR task's
 * own state (app/services/ocr_pipeline.py::OcrPipelineState) — it stops
 * itself on any error, so this is how the frontend finds out "still
 * going" vs "stopped, here's why". */
export interface ProgressResponse {
  pipeline_running: boolean
  pipeline_error: string | null
  items: ExamProgressItem[]
}

/** Mirrors Processor/backend/app/schemas/dto.py::OcrStartResponse —
 * POST /processor/ocr/start. Starting is idempotent: `already_running`
 * distinguishes "just started it" from "was already going". */
export interface OcrStartResponse {
  started: boolean
  already_running: boolean
}

/** Mirrors Processor/backend/app/schemas/dto.py::ResetResponse. */
export interface ResetResponse {
  exams_deleted: number
  pages_deleted: number
  storage_objects_deleted: number
  storage_objects_failed: string[]
}

/** Mirrors Processor/backend/app/schemas/dto.py::StatusCounts. */
export interface StatusCounts {
  pending: number
  processing: number
  finished: number
  failed: number
}

/** Mirrors Processor/backend/app/schemas/dto.py::DashboardResponse. No
 * more `active` (worker-claims list) — that live view now comes from
 * ProgressResponse above instead, surfaced on the exams section rather
 * than buried on this page. `db_size_bytes`/`storage_size_bytes` are
 * `null` when the backend's PROCESSOR_SUPABASE_ACCESS_TOKEN isn't
 * configured or the Management API call failed. */
export interface DashboardResponse {
  llamacpp_healthy: boolean
  finished_today: number
  failed_today: number
  counts: StatusCounts
  db_size_bytes: number | null
  storage_size_bytes: number | null
  /** Total pages currently admitted into 'processing' (queued + actively
   * being OCR'd) against `processing_pages_limit` — see
   * Processor/backend/app/services/queue_service.py::QueueService.admitted_pages.
   * Not the same as counts.processing (an exam count). */
  processing_pages: number
  processing_pages_limit: number
}
