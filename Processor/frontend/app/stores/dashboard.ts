import { apiErrorMessage } from '~/composables/useProcessorApi'
import type { ResetResponse, StatusCounts } from '~/types/models'

// Slower than exams.ts/other polling (5s) on purpose — db_size_bytes/
// storage_size_bytes go through Supabase's Management API (personal access
// token), which is meant for occasional/tooling use, not high-frequency
// polling like the PostgREST calls everything else here makes. Adjust once
// real rate-limit behavior is observed (see Processor/DESIGN_REPORT.md).
const POLL_MS = 15000

let pollTimer: ReturnType<typeof setInterval> | undefined

export const useDashboardStore = defineStore('dashboard', {
  state: () => ({
    llamacppHealthy: false,
    finishedToday: 0,
    failedToday: 0,
    counts: { pending: 0, processing: 0, finished: 0, failed: 0 } as StatusCounts,
    dbSizeBytes: null as number | null,
    storageSizeBytes: null as number | null,
    /** Pages, not exams — see types/models.ts's DashboardResponse docstring. */
    processingPages: 0,
    processingPagesLimit: 0,
    /** Processor API's own liveness (GET /health, no auth) — distinct from
     * `llamacppHealthy` above, which is the OCR model server's health as
     * reported by the backend's own health check. */
    apiHealthy: null as boolean | null,
    loading: false,
    offline: false,
    resetting: false,
    lastResetResult: null as ResetResponse | null
  }),

  actions: {
    async fetchDashboard() {
      this.loading = true
      const api = useProcessorApi()
      try {
        const [dashboard] = await Promise.all([
          api.getDashboard(),
          api.health().then(() => { this.apiHealthy = true }).catch(() => { this.apiHealthy = false })
        ])
        this.llamacppHealthy = dashboard.llamacpp_healthy
        this.finishedToday = dashboard.finished_today
        this.failedToday = dashboard.failed_today
        this.counts = dashboard.counts
        this.dbSizeBytes = dashboard.db_size_bytes
        this.storageSizeBytes = dashboard.storage_size_bytes
        this.processingPages = dashboard.processing_pages
        this.processingPagesLimit = dashboard.processing_pages_limit
        this.offline = false
      } catch (err) {
        this.offline = true
        // eslint-disable-next-line no-console
        console.error('[dashboard] fetchDashboard failed', err)
      } finally {
        this.loading = false
      }
    },

    startPolling() {
      if (pollTimer) return
      this.fetchDashboard()
      pollTimer = setInterval(() => this.fetchDashboard(), POLL_MS)
    },

    stopPolling() {
      clearInterval(pollTimer)
      pollTimer = undefined
    },

    async resetAll() {
      this.resetting = true
      try {
        this.lastResetResult = await useProcessorApi().resetAll()
        useToast().info('Đã xóa toàn bộ dữ liệu')
        await Promise.all([this.fetchDashboard(), useExamsStore().fetchLists()])
      } catch (err) {
        useToast().error(apiErrorMessage(err, 'Không thể xóa dữ liệu'))
      } finally {
        this.resetting = false
      }
    }
  }
})
