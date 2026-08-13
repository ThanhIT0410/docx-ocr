import type { RealtimeChannel } from '@supabase/supabase-js'
import type { ExamRecord, ExamStatus, ExamWithPages, PageRecord } from '~/types/models'

export interface ExamListItem extends ExamRecord {
  pageCount: number
}

export interface SignedPage extends PageRecord {
  signedUrl: string | null
}

// Deliberately NOT in Pinia state: Vue's reactive() wrapping strips the
// private class fields supabase-js's RealtimeChannel relies on for its
// nominal type, which both breaks TS (structural mismatch against
// RealtimeChannel) and risks confusing the client's internal state. This
// handle is write-once/read-once (subscribe/unsubscribe) and never rendered,
// so a plain module-level variable is the correct home for it.
let realtimeChannel: RealtimeChannel | null = null

export const useDocumentsStore = defineStore('documents', {
  state: () => ({
    pending: [] as ExamListItem[],
    processing: [] as ExamListItem[],
    finished: [] as ExamListItem[],
    recent: [] as ExamListItem[],
    activeExamId: null as string | null,
    activeExam: null as (Omit<ExamWithPages, 'pages'> & { pages: SignedPage[] }) | null,
    loadingLists: false,
    loadingActive: false,
    offline: false
  }),

  getters: {
    byStatus: (state) => (status: ExamStatus) =>
      status === 'pending' ? state.pending : status === 'processing' ? state.processing : state.finished
  },

  actions: {
    async fetchLists() {
      this.loadingLists = true
      const supabase = useSupabase()
      try {
        const { data, error } = await supabase
          .from('exams')
          .select('*, pages(count)')
          .order('uploaded_at', { ascending: false })
        if (error) throw error

        const rows = (data ?? []).map(row => rowToListItem(row))
        this.pending = rows.filter(r => r.status === 'pending')
        this.processing = rows.filter(r => r.status === 'processing')
        this.finished = rows.filter(r => r.status === 'finished')
        this.recent = rows.slice(0, 5)
        this.offline = false
      } catch (err) {
        this.offline = true
        useToast().error('Mất kết nối — không thể tải danh sách tài liệu.')
        // eslint-disable-next-line no-console
        console.error('[documents] fetchLists failed', err)
      } finally {
        this.loadingLists = false
      }
    },

    subscribeRealtime() {
      if (realtimeChannel) return
      const supabase = useSupabase()
      realtimeChannel = supabase
        .channel('exams-changes')
        .on('postgres_changes', { event: '*', schema: 'public', table: 'exams' }, () => {
          this.fetchLists()
          if (this.activeExamId) this.openExam(this.activeExamId)
        })
        .subscribe()
    },

    unsubscribeRealtime() {
      if (!realtimeChannel) return
      useSupabase().removeChannel(realtimeChannel)
      realtimeChannel = null
    },

    async openExam(examId: string) {
      this.loadingActive = true
      this.activeExamId = examId
      const supabase = useSupabase()
      try {
        const [{ data: exam, error: examErr }, { data: pages, error: pagesErr }] = await Promise.all([
          supabase.from('exams').select('*').eq('id', examId).single(),
          supabase.from('pages').select('*').eq('exam_id', examId).order('page_order', { ascending: true })
        ])
        if (examErr) throw examErr
        if (pagesErr) throw pagesErr

        const bucket = storageBucket()
        const signed: SignedPage[] = await Promise.all((pages ?? []).map(async (p) => {
          const { data } = await supabase.storage.from(bucket).createSignedUrl(p.file_path, 3600)
          return { ...p, signedUrl: data?.signedUrl ?? null }
        }))

        this.activeExam = { ...(exam as ExamRecord), pages: signed }
        this.offline = false
      } catch (err) {
        this.offline = true
        useToast().error('Mất kết nối — không thể tải tài liệu này.')
        // eslint-disable-next-line no-console
        console.error('[documents] openExam failed', err)
      } finally {
        this.loadingActive = false
      }
    },

    /** Pending editor "Lưu thay đổi": persists reordering + deletions. */
    async savePendingChanges(examId: string, orderedPageIds: string[], deletedPages: SignedPage[]) {
      const supabase = useSupabase()
      const bucket = storageBucket()
      try {
        if (deletedPages.length) {
          await supabase.storage.from(bucket).remove(deletedPages.map(p => p.file_path))
          const { error } = await supabase.from('pages').delete().in('id', deletedPages.map(p => p.id))
          if (error) throw error
        }
        // page_order must stay unique per exam at every intermediate step, so
        // push everything past the max first, then apply final values.
        const bump = orderedPageIds.map((id, i) =>
          supabase.from('pages').update({ page_order: 1000 + i }).eq('id', id))
        await Promise.all(bump)
        const final = orderedPageIds.map((id, i) =>
          supabase.from('pages').update({ page_order: i + 1 }).eq('id', id))
        await Promise.all(final)

        useToast().info('Đã lưu thứ tự trang')
        await this.openExam(examId)
      } catch (err) {
        useToast().error('Không thể lưu thay đổi — kiểm tra kết nối mạng.')
        // eslint-disable-next-line no-console
        console.error('[documents] savePendingChanges failed', err)
      }
    },

    /** Deletes one exam entirely (pages cascade via FK; Storage objects
     * don't, so those are removed explicitly first). Blocked while
     * `status === 'processing'` — checked client-side first for instant
     * feedback, and enforced again by the `exams_delete_anon` RLS policy
     * (supabase/schema.sql) in case the status flips between that check
     * and the actual delete. */
    async deleteExam(examId: string, status: ExamStatus) {
      if (status === 'processing') {
        await useConfirm().alert({
          title: 'Không thể xóa lúc này',
          message: 'Đề này đang được xử lý nên chưa thể xóa. Vui lòng đợi xử lý xong (hoặc thất bại) rồi thử lại.'
        })
        return
      }

      const ok = await useConfirm().confirm({
        title: 'Xóa đề này?',
        message: 'Toàn bộ trang và kết quả nhận dạng (nếu có) sẽ bị xóa vĩnh viễn. Không thể hoàn tác.',
        danger: true,
        confirmLabel: 'Xóa đề'
      })
      if (!ok) return

      const supabase = useSupabase()
      const bucket = storageBucket()
      try {
        const { data: pages, error: pagesErr } = await supabase.from('pages').select('file_path').eq('exam_id', examId)
        if (pagesErr) throw pagesErr
        if (pages?.length) {
          await supabase.storage.from(bucket).remove(pages.map(p => p.file_path))
        }

        const { data, error } = await supabase.from('exams').delete().eq('id', examId).select('id')
        if (error) throw error

        if (!data || data.length === 0) {
          // RLS silently filtered the row out — status flipped to
          // 'processing' in the gap between our check above and this
          // delete. Not an error, just too late.
          await useConfirm().alert({
            title: 'Không thể xóa lúc này',
            message: 'Đề vừa chuyển sang trạng thái đang xử lý nên chưa thể xóa. Vui lòng thử lại sau.'
          })
          await this.fetchLists()
          return
        }

        useToast().info('Đã xóa đề')
        if (this.activeExamId === examId) {
          this.activeExamId = null
          this.activeExam = null
          await navigateTo('/documents')
        }
        await this.fetchLists()
      } catch (err) {
        useToast().error('Không thể xóa đề — kiểm tra kết nối mạng.')
        // eslint-disable-next-line no-console
        console.error('[documents] deleteExam failed', err)
      }
    }
  }
})

function rowToListItem(row: any): ExamListItem {
  const pageCount = Array.isArray(row.pages) ? (row.pages[0]?.count ?? 0) : 0
  const { pages: _pages, ...exam } = row
  return { ...(exam as ExamRecord), pageCount }
}
