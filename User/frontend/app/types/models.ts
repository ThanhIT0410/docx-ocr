export type ExamStatus = 'pending' | 'processing' | 'finished'

/** Mirrors the `exams` table — see supabase/schema.sql */
export interface ExamRecord {
  id: string
  title: string
  status: ExamStatus
  error_message: string | null
  uploaded_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

/** One layout block within a page — the model's raw HTML response
 * (`<div data-bbox="..." data-label="...">`) parsed server-side into
 * structured form by Processor/backend/app/services/postprocessing.py.
 * `bbox` is `[x1, y1, x2, y2]` in **input** pixel space (see
 * `OcrPageResult.inputWidth/Height` below) — i.e. relative to the resized
 * image actually sent to the model, not the original page image. `text`
 * may contain markdown-lite markup (`**bold**`, `*italic*`, `__underline__`)
 * or, for `category === 'Table' | 'List-item'`, raw HTML. */
export interface DocumentLayout {
  bbox: number[]
  category: string
  text: string
  /** Heading depth 1-5, only set when `category === 'Section-header'`. */
  level?: number | null
}

/** Structured OCR payload stored in `pages.ocr_text` (JSONB) — the full
 * result for one page. Mirrors `OcrPageResult` in
 * Processor/backend/app/schemas/models.py exactly. */
export interface OcrPageResult {
  origin_width: number
  origin_height: number
  input_width: number
  input_height: number
  layouts: DocumentLayout[]
}

/** Mirrors the `pages` table — see supabase/schema.sql */
export interface PageRecord {
  id: string
  exam_id: string
  page_order: number
  file_path: string
  ocr_text: OcrPageResult | null
}

export interface ExamWithPages extends ExamRecord {
  pages: PageRecord[]
}

/** A page still local to this machine — not yet uploaded to Supabase. It
 * comes from the FastAPI /preview endpoint (PDF split) or directly from a
 * picked image file. */
export interface LocalPreviewPage {
  id: string
  /** Thumbnail URL served by the sidecar (GET /preview/{previewId}/pages/{id}). */
  filePath: string
  /** Human label of where this page came from, e.g. the source file name. */
  source: string
  order: number
  /** Which sidecar preview job this page belongs to — needed to issue a
   * PATCH /preview/{previewId} delete when the user removes this page. */
  previewId: string
}

/** One file/batch queued in the upload flow, step 1 — a *component* of the
 * exam being built (e.g. one PDF, or one batch of images), not an exam in
 * its own right. Not user-nameable: the exam itself is named once, at
 * step 2 (see stores/upload.ts's `examTitle`). */
export interface UploadItem {
  id: string
  /** Original file/folder name — shown as-is, never user-edited. */
  fileName: string
  kind: 'pdf' | 'img'
  /** Sidecar preview id once a PDF split job has been created. */
  previewId?: string
  splitting: boolean
  pages: LocalPreviewPage[]
  error?: string
}
