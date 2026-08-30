import type { LocalPreviewPage, UploadItem } from '~/types/models'

let uid = 0
const nextId = () => `u${Date.now()}_${++uid}`

const IMAGE_TYPES = ['image/jpeg', 'image/png']
const PDF_TYPE = 'application/pdf'
const MAX_FILE_BYTES = 50 * 1024 * 1024

export const useUploadStore = defineStore('upload', {
  state: () => ({
    step: 1 as 1 | 2,
    items: [] as UploadItem[],
    pages: [] as LocalPreviewPage[],
    /** The exam's name — the only name that matters (see UploadItem's doc
     * comment). Editable at step 2, defaulted once when entering it. */
    examTitle: '',
    submitting: false
  }),

  getters: {
    /** "Tiếp tục" is locked until every queued item finished splitting. */
    readyForStep2: (state) => state.items.length > 0 && state.items.every(i => !i.splitting && !i.error)
  },

  actions: {
    /** Splits a drop/pick batch: each PDF becomes its own "đề"; all images in
     * the same batch become a single "đề" (bộ ảnh) — per §5. */
    async addFiles(fileList: FileList | File[]) {
      const files = Array.from(fileList)
      const oversized = files.filter(f => f.size > MAX_FILE_BYTES)
      if (oversized[0]) {
        useToast().error(`Tệp "${oversized[0].name}" vượt quá 50MB, vui lòng chọn tệp nhỏ hơn.`)
      }
      const valid = files.filter(f => f.size <= MAX_FILE_BYTES && (f.type === PDF_TYPE || IMAGE_TYPES.includes(f.type)))
      if (!valid.length) return

      const pdfFiles = valid.filter(f => f.type === PDF_TYPE)
      const imageFiles = valid.filter(f => f.type !== PDF_TYPE)

      const batches: Array<{ kind: 'pdf' | 'img', name: string, files: File[] }> = [
        ...pdfFiles.map(f => ({ kind: 'pdf' as const, name: f.name, files: [f] })),
        ...(imageFiles.length ? [{ kind: 'img' as const, name: `Bộ ảnh (${imageFiles.length} ảnh)`, files: imageFiles }] : [])
      ]

      for (const batch of batches) {
        const item: UploadItem = {
          id: nextId(),
          fileName: batch.name,
          kind: batch.kind,
          splitting: true,
          pages: []
        }
        this.items.push(item)
        this.runPreview(item.id, batch.files)
      }
    },

    /** Takes an id, not the item object itself: `item` at the addFiles()
     * call site is the raw object literal, captured *before*
     * `this.items.push()` puts it under Pinia's reactive proxy — mutating
     * that raw reference later (once this async call resolves) never
     * triggers a re-render, since Vue's reactivity only reacts to writes
     * that go through the proxy. Always re-fetching via `this.items.find()`
     * (both before and after the await) keeps every mutation reactive, and
     * also means a page removed from the queue mid-request is handled
     * gracefully instead of resurrecting a stale entry. */
    async runPreview(itemId: string, files: File[]) {
      const item = this.items.find(i => i.id === itemId)
      if (!item) return
      try {
        // `title` here is only a label for the sidecar's on-disk preview
        // folder (never shown to the user, never becomes exams.title —
        // see UploadItem's doc comment), so a derived-from-filename value
        // is fine without being user-editable.
        const { previewId, pages } = await useSidecar().createPreview(defaultTitle(item.fileName), files)
        const current = this.items.find(i => i.id === itemId)
        if (!current) return
        current.previewId = previewId
        current.pages = pages
        current.splitting = false
        if (current.kind === 'pdf') useToast().info(`Đã tách "${current.fileName}" thành ${pages.length} trang`)
      } catch (err) {
        const current = this.items.find(i => i.id === itemId)
        if (current) {
          current.splitting = false
          current.error = 'Không thể xử lý tệp này'
        }
        useToast().error(`Lỗi xử lý "${item.fileName}" — kiểm tra tiến trình nền (sidecar) có đang chạy không.`)
        // eslint-disable-next-line no-console
        console.error('[upload] preview failed', err)
      }
    },

    async removeItem(id: string) {
      const idx = this.items.findIndex(i => i.id === id)
      if (idx === -1) return
      const [item] = this.items.splice(idx, 1)
      if (item?.previewId) {
        try { await useSidecar().deletePreview(item.previewId) } catch { /* best-effort cleanup */ }
      }
    },

    setItemsOrder(orderedIds: string[]) {
      const byId = new Map(this.items.map(i => [i.id, i]))
      this.items = orderedIds.map(id => byId.get(id)!).filter(Boolean)
    },

    goToStep2() {
      this.pages = this.items.flatMap(i => i.pages).map((p, i) => ({ ...p, order: i + 1 }))
      // Only suggest a default the first time — re-entering step 2 (e.g.
      // after going back to add one more file) must not clobber a title
      // the user already typed.
      if (!this.examTitle && this.items[0]) this.examTitle = defaultTitle(this.items[0].fileName)
      this.step = 2
    },

    setExamTitle(title: string) {
      this.examTitle = title
    },

    backToStep1() {
      this.step = 1
    },

    setPagesOrder(orderedIds: string[]) {
      const byId = new Map(this.pages.map(p => [p.id, p]))
      this.pages = orderedIds.map(id => byId.get(id)!).filter(Boolean)
    },

    async removePage(id: string) {
      const idx = this.pages.findIndex(p => p.id === id)
      if (idx === -1) return
      const [page] = this.pages.splice(idx, 1)
      if (!page) return
      try {
        await useSidecar().patchPreview(page.previewId, { deletePageIds: [page.id] })
      } catch {
        useToast().error('Không thể xóa trang trên đĩa cục bộ (đã ẩn khỏi danh sách).')
      }
    },

    /** Uploads page images to Supabase Storage, writes exams+pages rows, then
     * cleans up local sidecar previews. Matches §9.1 "Submit". */
    async submit() {
      if (!this.pages.length || this.submitting) return
      this.submitting = true
      const supabase = useSupabase()
      const bucket = storageBucket()
      const title = this.examTitle || `Đề mới ${new Date().toLocaleString('vi-VN')}`

      try {
        const { data: exam, error: examErr } = await supabase
          .from('exams')
          .insert({ title, status: 'pending' })
          .select()
          .single()
        if (examErr) throw examErr

        for (const [i, page] of this.pages.entries()) {
          const res = await fetch(page.filePath)
          if (!res.ok) throw new Error(`Không tải được ảnh trang từ tiến trình nền (${res.status})`)
          const blob = await res.blob()
          const objectKey = `${exam.id}/${page.id}.png`

          const { error: upErr } = await supabase.storage.from(bucket).upload(objectKey, blob, {
            contentType: blob.type || 'image/png',
            upsert: false
          })
          if (upErr) throw upErr

          const { error: pageErr } = await supabase.from('pages').insert({
            exam_id: exam.id,
            page_order: i + 1,
            file_path: objectKey
          })
          if (pageErr) throw pageErr
        }

        const previewIds = [...new Set(this.items.map(i => i.previewId).filter((x): x is string => !!x))]
        await Promise.allSettled(previewIds.map(id => useSidecar().deletePreview(id)))

        this.items = []
        this.pages = []
        this.examTitle = ''
        this.step = 1
        useToast().info('Đã gửi đề — đang chờ xử lý')
        await useDocumentsStore().fetchLists()
      } catch (err) {
        useToast().error('Gửi đề thất bại — kiểm tra kết nối mạng rồi thử lại.')
        // eslint-disable-next-line no-console
        console.error('[upload] submit failed', err)
      } finally {
        this.submitting = false
      }
    }
  }
})

function defaultTitle(fileName: string): string {
  return fileName.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ')
}
