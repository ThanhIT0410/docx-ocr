import { apiErrorMessage } from '~/composables/useProcessorApi'
import type { ExamProgressItem } from '~/types/models'

// Liveliest data in the app now (per-page OCR progress) — polled faster
// than exams.ts's list (5s) but not so fast it hammers the backend for a
// number that only moves once every model call. Only worth that cadence
// while a run is actually in flight, though — a "run" now stops itself
// once the queue drains (see ocr_pipeline.py's run_pipeline), so most of
// the time there's nothing moving to poll for.
//
// No polling at all happens until `start()` actually launches a run —
// not even one fetch on mount. That's safe specifically because a fresh
// backend process always begins with pipeline_running=false (nothing
// auto-starts it — see ocr_pipeline.py/controllers/ocr.py), so the
// store's own initial state (`running: false` below) is already correct
// on a cold app launch without needing to ask the backend to confirm it.
// `running` only ever becomes true as a direct result of this store's own
// `start()` action succeeding, and this same store instance (a
// module-level singleton, like every Pinia store) keeps that state in
// memory across page navigations for the rest of the session — so a page
// mounting later just reads the already-known state instead of
// re-fetching it. The one thing this does NOT cover: a renderer reload
// (dev-mode HMR, Ctrl+R) while the backend process is still alive from
// before and mid-run — an acceptable gap, since the packaged app has no
// such "reload renderer without restarting backend" path.
const POLL_MS = 2500

let pollTimer: ReturnType<typeof setInterval> | undefined
// How many mounted components currently want polling active (startPolling
// call sites: exams/index.vue, exams/[id].vue, dashboard.vue) — needed
// because _syncPollingCadence may stop/restart the shared interval based
// on `running` flips, independent of any one component's mount/unmount.
let pollWanters = 0

export const usePipelineStore = defineStore('pipeline', {
  state: () => ({
    running: false,
    error: null as string | null,
    items: [] as ExamProgressItem[],
    loading: false,
    offline: false,
    starting: false
  }),

  getters: {
    /** Live progress for one exam, if the OCR pipeline has actually
     * started working on it (vs. still just sitting in the queue). */
    byExamId: (state) => (examId: string): ExamProgressItem | null =>
      state.items.find(i => i.exam_id === examId) ?? null
  },

  actions: {
    async fetchProgress() {
      this.loading = true
      try {
        const res = await useProcessorApi().getQueueProgress()
        this.running = res.pipeline_running
        this.error = res.pipeline_error
        this.items = res.items
        this.offline = false
      } catch (err) {
        this.offline = true
        // eslint-disable-next-line no-console
        console.error('[pipeline] fetchProgress failed', err)
      } finally {
        this.loading = false
      }
      this._syncPollingCadence()
    },

    /** Keeps the shared interval alive only while there's something worth
     * repeatedly polling for (`running`) and at least one component still
     * wants polling at all — called after every fetch so a run finishing
     * (running flips to false) tears the interval down on its own, no
     * separate "did it just stop" check needed. */
    _syncPollingCadence() {
      const shouldPoll = pollWanters > 0 && this.running
      if (shouldPoll && !pollTimer) {
        pollTimer = setInterval(() => this.fetchProgress(), POLL_MS)
      } else if (!shouldPoll && pollTimer) {
        clearInterval(pollTimer)
        pollTimer = undefined
      }
    },

    startPolling() {
      pollWanters += 1
      // No fetch here on purpose (see module docstring) — just registers
      // this component as wanting polling *if* a run is already in
      // flight from earlier this session. If `running` is already true
      // (e.g. navigated away mid-run and back), this resumes the shared
      // interval; if idle, it stays a no-op until `start()` is called.
      this._syncPollingCadence()
    },

    stopPolling() {
      pollWanters = Math.max(0, pollWanters - 1)
      this._syncPollingCadence()
    },

    async start() {
      this.starting = true
      try {
        const res = await useProcessorApi().startOcr()
        useToast().info(res.already_running ? 'OCR đang chạy sẵn rồi' : 'Đã bật xử lý OCR')
        await this.fetchProgress()
      } catch (err) {
        useToast().error(apiErrorMessage(err, 'Không thể bật xử lý OCR'))
      } finally {
        this.starting = false
      }
    }
  }
})
