import type {
  DashboardResponse,
  DequeueResponse,
  EnqueueResponse,
  ExamDetail,
  ExamListItem,
  ExamPreviewResponse,
  ExamStatus,
  OcrStartResponse,
  ProgressResponse,
  ResetResponse
} from '~/types/models'

/** Thin wrapper around Processor/backend's admin API (app/controllers/*.py).
 * Every call goes through here so the X-API-Key/X-Admin-API-Key headers
 * (app/security.py) are attached exactly once, in exactly one place.
 *
 * Base URL/keys resolution:
 *  - Packaged/dev Electron: injected by the main process via preload
 *    (window.processorBridge), since the port and both keys are generated
 *    fresh at every launch (see electron/sidecar.js — the admin API is
 *    spawned locally now, not a separately-deployed service you point at).
 *  - Plain `nuxt dev` in a browser tab (no Electron): falls back to
 *    NUXT_PUBLIC_PROCESSOR_API_*, useful for iterating on the UI against a
 *    manually-run `python -m app.main`. */
export function useProcessorApi() {
  const bridge = useElectronBridge()
  const config = useRuntimeConfig()
  const baseURL = bridge?.apiBaseUrl || (config.public.processorApiUrl as string)
  const apiKey = bridge?.apiKey || (config.public.processorApiKey as string)
  const adminApiKey = bridge?.adminApiKey || (config.public.processorAdminApiKey as string)

  if (!baseURL || !apiKey) {
    // eslint-disable-next-line no-console
    console.error(
      '[useProcessorApi] Không có base URL/API key. Trong Electron, kiểm tra log của tiến trình ' +
      'backend (main.js); ở chế độ browser dev, kiểm tra NUXT_PUBLIC_PROCESSOR_API_URL/_API_KEY (.env).'
    )
  }

  function call<T>(path: string, opts: Parameters<typeof $fetch>[1] = {}): Promise<T> {
    return $fetch<T>(path, {
      baseURL,
      headers: { 'X-API-Key': apiKey, ...(opts.headers as Record<string, string> | undefined) },
      ...opts
    })
  }

  return {
    health: () => $fetch<{ status: string }>('/health', { baseURL }),

    listExams: (status?: ExamStatus) =>
      call<ExamListItem[]>('/processor/exams', { query: status ? { status } : undefined }),

    getExam: (examId: string) => call<ExamDetail>(`/processor/exams/${examId}`),

    /** Signed image URLs for every page of an exam — a separate,
     * on-demand call (not part of getExam's polling), see
     * ExamPreviewResponse's doc comment. */
    getExamPreview: (examId: string) => call<ExamPreviewResponse>(`/processor/exams/${examId}/preview`),

    enqueueExams: (examIds: string[]) =>
      call<EnqueueResponse>('/processor/queue/enqueue', { method: 'POST', body: { exam_ids: examIds } }),

    dequeueExams: (examIds: string[]) =>
      call<DequeueResponse>('/processor/queue/dequeue', { method: 'POST', body: { exam_ids: examIds } }),

    getQueueProgress: () => call<ProgressResponse>('/processor/queue/progress'),

    startOcr: () => call<OcrStartResponse>('/processor/ocr/start', { method: 'POST' }),

    getDashboard: () => call<DashboardResponse>('/processor/dashboard'),

    resetAll: () =>
      call<ResetResponse>('/processor/admin/reset', {
        method: 'POST',
        headers: { 'X-Admin-API-Key': adminApiKey }
      })
  }
}

/** Extracts the `detail` message FastAPI's HTTPException puts in the
 * response body, falling back to a generic message — used by stores/pages
 * to show a specific toast instead of "unknown error". */
export function apiErrorMessage(err: unknown, fallback: string): string {
  const data = (err as { data?: { detail?: string } } | undefined)?.data
  return data?.detail ?? fallback
}
