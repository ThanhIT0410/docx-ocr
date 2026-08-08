import type { LocalPreviewPage } from '~/types/models'

/**
 * Client for the local FastAPI sidecar (PDF page-splitting + export).
 * Contract lives in User/backend/app/routers — keep both in sync.
 *
 * Base URL resolution:
 *  - Packaged/dev Electron: injected by the main process via preload
 *    (window.ocrBridge.sidecarBaseUrl), because the port is chosen at
 *    runtime (see electron/sidecar.js — findFreePort).
 *  - Plain `nuxt dev` in a browser tab (no Electron): falls back to
 *    NUXT_PUBLIC_SIDECAR_URL, useful for iterating on the UI only.
 */
function baseUrl(): string {
  const bridge = useElectronBridge()
  if (bridge?.sidecarBaseUrl) return bridge.sidecarBaseUrl
  return useRuntimeConfig().public.sidecarUrl
}

interface PreviewPageDto {
  id: string
  order: number
  source: string
}

interface PreviewDto {
  previewId: string
  title: string
  pages: PreviewPageDto[]
  createdAt: string
}

function toLocalPages(previewId: string, dto: PreviewPageDto[]): LocalPreviewPage[] {
  return dto
    .slice()
    .sort((a, b) => a.order - b.order)
    .map(p => ({
      id: p.id,
      order: p.order,
      source: p.source,
      filePath: pageThumbnailUrl(previewId, p.id),
      previewId
    }))
}

/** Direct <img src> URL served by the sidecar for a given preview page. */
export function pageThumbnailUrl(previewId: string, pageId: string): string {
  return `${baseUrl()}/preview/${previewId}/pages/${pageId}`
}

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`Sidecar ${res.status}: ${body || res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function useSidecar() {
  /** Upload one "đề" (a PDF or a batch of images) for local processing.
   * PDFs are split into pages by PyMuPDF server-side; image batches are
   * copied as-is, one page per image. */
  async function createPreview(title: string, files: File[]): Promise<{ previewId: string, pages: LocalPreviewPage[] }> {
    const form = new FormData()
    form.append('title', title)
    for (const f of files) form.append('files', f, f.name)

    const res = await fetch(`${baseUrl()}/preview`, { method: 'POST', body: form })
    const dto = await asJson<PreviewDto>(res)
    return { previewId: dto.previewId, pages: toLocalPages(dto.previewId, dto.pages) }
  }

  /** Reorder / rename / delete specific pages of an existing preview. */
  async function patchPreview(previewId: string, patch: {
    title?: string
    pageOrder?: string[]
    deletePageIds?: string[]
  }): Promise<LocalPreviewPage[]> {
    const res = await fetch(`${baseUrl()}/preview/${previewId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    })
    const dto = await asJson<PreviewDto>(res)
    return toLocalPages(previewId, dto.pages)
  }

  /** Discard a preview entirely (removes its temp files from disk). */
  async function deletePreview(previewId: string): Promise<void> {
    const res = await fetch(`${baseUrl()}/preview/${previewId}`, { method: 'DELETE' })
    if (!res.ok && res.status !== 404) throw new Error(`Sidecar ${res.status}`)
  }

  /** Previews left over from a session that quit mid-upload (§10 "Dọn dẹp"). */
  async function listDanglingPreviews(): Promise<PreviewDto[]> {
    const res = await fetch(`${baseUrl()}/preview`)
    return asJson<PreviewDto[]>(res)
  }

  /** Runs the result-composition pipeline (see backend/app/services/
   * export_service.py) and returns the exported .docx for the caller to
   * save. The filename/extension come from the server
   * (Content-Disposition) rather than being assumed client-side, so a
   * future format change doesn't need an API contract change here. */
  async function exportExam(payload: {
    examId: string
    title: string
    pages: Array<{ pageId: string, order: number, ocrText: unknown, originalImageUrl: string }>
    /** 'layout' (default, server-side) runs the full column/section
     * reconstruction pipeline; 'plain' appends each block's text
     * sequentially, ignoring layout — see backend's
     * ExportRequest.mode / create_export_service(). */
    mode?: 'layout' | 'plain'
  }): Promise<{ blob: Blob, filename: string }> {
    const res = await fetch(`${baseUrl()}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!res.ok) throw new Error(`Sidecar ${res.status}: ${await res.text().catch(() => res.statusText)}`)
    const disposition = res.headers.get('Content-Disposition') ?? ''
    const match = /filename="?([^"]+)"?/.exec(disposition)
    const filename = match?.[1] ?? `${payload.title}.bin`
    return { blob: await res.blob(), filename }
  }

  async function health(): Promise<boolean> {
    try {
      const res = await fetch(`${baseUrl()}/health`)
      return res.ok
    } catch {
      return false
    }
  }

  return { createPreview, patchPreview, deletePreview, listDanglingPreviews, exportExam, health }
}
