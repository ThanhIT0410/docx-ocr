import { apiErrorMessage } from '~/composables/useProcessorApi'
import type { DequeueItemResult, EnqueueItemResult, ExamDetail, ExamListItem, ExamStatus, RetryItemResult } from '~/types/models'

const LIST_POLL_MS = 5000
const DETAIL_POLL_MS = 3000

// Timer handles deliberately live outside Pinia state (same reasoning as
// User/frontend's realtimeChannel: these aren't renderable data, and
// reactive() wrapping a timer handle has no upside).
let listTimer: ReturnType<typeof setInterval> | undefined
let detailTimer: ReturnType<typeof setInterval> | undefined

/** The only groups a checkbox/bulk action ever appears in — 'finished'
 * exams have no action to offer. 'failed' offers retry (back to pending),
 * distinct from 'pending' (enqueue) and 'processing' (dequeue). */
type SelectableGroup = 'pending' | 'processing' | 'failed'

export const useExamsStore = defineStore('exams', {
  state: () => ({
    pending: [] as ExamListItem[],
    processing: [] as ExamListItem[],
    finished: [] as ExamListItem[],
    failed: [] as ExamListItem[],
    activeExamId: null as string | null,
    activeExam: null as ExamDetail | null,
    loadingLists: false,
    loadingActive: false,
    offline: false,
    // A selection only ever spans one group at a time — the bulk action
    // available (enqueue vs dequeue) depends on which, so mixing them
    // would be ambiguous. See selectRow/selectAllInGroup below.
    selectedGroup: null as SelectableGroup | null,
    selectedIds: new Set<string>()
  }),

  getters: {
    byStatus: (state) => (status: ExamStatus) => {
      if (status === 'pending') return state.pending
      if (status === 'processing') return state.processing
      if (status === 'finished') return state.finished
      return state.failed
    },
    selectedCount: (state) => state.selectedIds.size
  },

  actions: {
    async fetchLists() {
      this.loadingLists = true
      try {
        const rows = await useProcessorApi().listExams()
        this.pending = rows.filter(r => r.status === 'pending')
        this.processing = rows.filter(r => r.status === 'processing')
        this.finished = rows.filter(r => r.status === 'finished')
        this.failed = rows.filter(r => r.status === 'failed')
        this.offline = false
        this._pruneSelection()
      } catch (err) {
        this.offline = true
        // eslint-disable-next-line no-console
        console.error('[exams] fetchLists failed', err)
      } finally {
        this.loadingLists = false
      }
    },

    startListPolling() {
      if (listTimer) return
      this.fetchLists()
      listTimer = setInterval(() => this.fetchLists(), LIST_POLL_MS)
    },

    stopListPolling() {
      clearInterval(listTimer)
      listTimer = undefined
    },

    async openExam(examId: string) {
      this.loadingActive = true
      this.activeExamId = examId
      try {
        this.activeExam = await useProcessorApi().getExam(examId)
        this.offline = false
      } catch (err) {
        this.offline = true
        // eslint-disable-next-line no-console
        console.error('[exams] openExam failed', err)
      } finally {
        this.loadingActive = false
      }
      this._syncDetailPolling()
    },

    /** Only polls the detail view while there's something moving to watch —
     * a 'finished'/'failed' exam's detail never changes on its own. */
    _syncDetailPolling() {
      clearInterval(detailTimer)
      detailTimer = undefined
      if (this.activeExam?.status !== 'processing') return
      const examId = this.activeExam.id
      detailTimer = setInterval(() => {
        if (this.activeExamId === examId) this.openExam(examId)
      }, DETAIL_POLL_MS)
    },

    stopDetailPolling() {
      clearInterval(detailTimer)
      detailTimer = undefined
      this.activeExamId = null
      this.activeExam = null
    },

    // ---- Selection (bulk enqueue/dequeue) --------------------------------

    toggleRow(examId: string, group: SelectableGroup) {
      if (this.selectedGroup !== group) {
        this.selectedGroup = group
        this.selectedIds = new Set([examId])
        return
      }
      const next = new Set(this.selectedIds)
      if (next.has(examId)) next.delete(examId)
      else next.add(examId)
      this.selectedIds = next
      if (next.size === 0) this.selectedGroup = null
    },

    selectAllInGroup(group: SelectableGroup) {
      const ids = this.byStatus(group).map(e => e.id)
      this.selectedGroup = ids.length ? group : null
      this.selectedIds = new Set(ids)
    },

    clearSelection() {
      this.selectedGroup = null
      this.selectedIds = new Set()
    },

    /** Drops any selected id that's no longer in its group after a
     * refetch (e.g. it got enqueued by someone else, or finished) —
     * otherwise a stale selection could linger and confuse the bulk bar. */
    _pruneSelection() {
      if (!this.selectedGroup || this.selectedIds.size === 0) return
      const liveIds = new Set(this.byStatus(this.selectedGroup).map(e => e.id))
      const next = new Set([...this.selectedIds].filter(id => liveIds.has(id)))
      this.selectedIds = next
      if (next.size === 0) this.selectedGroup = null
    },

    // ---- Actions ----------------------------------------------------------

    async enqueue(examIds: string[]) {
      try {
        const res = await useProcessorApi().enqueueExams(examIds)
        this._toastBatchResult(res.results, r => r.queued, (n) => `Đã đưa ${n} đề vào hàng đợi xử lý`)
        this.clearSelection()
        await this.fetchLists()
        if (this.activeExamId && examIds.includes(this.activeExamId)) await this.openExam(this.activeExamId)
      } catch (err) {
        useToast().error(apiErrorMessage(err, 'Không thể đưa đề vào hàng đợi'))
      }
    },

    async dequeue(examIds: string[]) {
      try {
        const res = await useProcessorApi().dequeueExams(examIds)
        this._toastBatchResult(res.results, r => r.dequeued, (n) => `Đã rút ${n} đề khỏi hàng đợi`)
        this.clearSelection()
        await this.fetchLists()
        if (this.activeExamId && examIds.includes(this.activeExamId)) await this.openExam(this.activeExamId)
      } catch (err) {
        useToast().error(apiErrorMessage(err, 'Không thể rút đề khỏi hàng đợi'))
      }
    },

    async retry(examIds: string[]) {
      try {
        const res = await useProcessorApi().retryExams(examIds)
        this._toastBatchResult(res.results, r => r.retried, (n) => `Đã đưa ${n} đề về chờ xử lý — cần vào hàng đợi lại`)
        this.clearSelection()
        await this.fetchLists()
        if (this.activeExamId && examIds.includes(this.activeExamId)) await this.openExam(this.activeExamId)
      } catch (err) {
        useToast().error(apiErrorMessage(err, 'Không thể thử lại đề'))
      }
    },

    _toastBatchResult<T extends { reason: string | null }>(
      results: T[],
      isOk: (r: T) => boolean,
      okMessage: (n: number) => string
    ) {
      const ok = results.filter(isOk).length
      const failedOnes = results.filter(r => !isOk(r))
      if (ok && !failedOnes.length) {
        useToast().info(okMessage(ok))
      } else if (ok && failedOnes.length) {
        useToast().info(`${okMessage(ok)} — ${failedOnes.length} đề không thể xử lý`)
      } else if (failedOnes.length) {
        useToast().error(failedOnes[0]?.reason ?? 'Thao tác không thành công')
      }
    }
  }
})

export type { DequeueItemResult, EnqueueItemResult, RetryItemResult }
