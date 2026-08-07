<script setup lang="ts">
import { Download } from '@lucide/vue'
import type { DocumentLayout } from '~/types/models'

const props = defineProps<{ exam: NonNullable<ReturnType<typeof useDocumentsStore>['activeExam']> }>()

const exporting = ref(false)
const leftPane = ref<HTMLElement>()
const rightPane = ref<HTMLElement>()

/** `text` may be raw HTML for category === 'Table' | 'List-item' (see
 * Processor's postprocessing.py::clean_html) — this preview only needs
 * readable plain text, not full table/list fidelity (that's the export
 * pipeline's job, see User/backend/app/services/export_service.py). Using
 * DOMParser rather than `v-html` is
 * deliberate: a parsed-but-never-attached document never executes
 * embedded scripts/handlers, so this stays safe even if the model ever
 * emits unexpected markup. */
function plainText(text: string): string {
  return new DOMParser().parseFromString(text, 'text/html').body.textContent ?? ''
}

function layoutBlocks(raw: unknown): DocumentLayout[] {
  if (raw && typeof raw === 'object' && Array.isArray((raw as { layouts?: unknown }).layouts)) {
    return (raw as { layouts: DocumentLayout[] }).layouts
  }
  return []
}

async function exportResult() {
  exporting.value = true
  useToast().info('Đang xuất kết quả OCR…')
  try {
    const { blob, filename } = await useSidecar().exportExam({
      examId: props.exam.id,
      title: props.exam.title,
      pages: props.exam.pages.map(p => ({
        pageId: p.id,
        order: p.page_order,
        ocrText: p.ocr_text,
        originalImageUrl: p.signedUrl ?? ''
      }))
    })
    await saveExportedFile(blob, filename)
    useToast().info('Đã xuất kết quả')
  } catch (err) {
    useToast().error('Xuất kết quả thất bại — kiểm tra tiến trình nền (sidecar) có đang chạy không.')
    // eslint-disable-next-line no-console
    console.error('[finished] export failed', err)
  } finally {
    exporting.value = false
  }
}

async function saveExportedFile(blob: Blob, suggestedName: string) {
  const bridge = useElectronBridge()
  if (bridge && (window as any).ocrBridge?.saveFile) {
    const buf = await blob.arrayBuffer()
    await (window as any).ocrBridge.saveFile(buf, suggestedName)
    return
  }
  // Browser fallback (plain `nuxt dev`, no Electron).
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = suggestedName
  a.click()
  URL.revokeObjectURL(url)
}

let scrollLock = false
function syncScroll(from: HTMLElement, to: HTMLElement) {
  if (scrollLock) { scrollLock = false; return }
  scrollLock = true
  const ratio = from.scrollTop / ((from.scrollHeight - from.clientHeight) || 1)
  to.scrollTop = ratio * (to.scrollHeight - to.clientHeight)
}
</script>

<template>
  <div class="finished-root">
    <div class="main-head">
      <div>
        <div class="main-title">{{ exam.title }}</div>
        <div class="main-sub">{{ exam.pages.length }} trang · hoàn thành {{ formatDateTime(exam.finished_at) }}</div>
      </div>
      <span class="spacer" />
      <StatusBadge status="finished" />
      <button
        class="btn btn-primary"
        title="Tải kết quả nhận dạng chữ (OCR) về máy của bạn"
        :disabled="exporting"
        @click="exportResult"
      >
        <Download :size="17" /> {{ exporting ? 'Đang xuất…' : 'Xuất kết quả' }}
      </button>
    </div>

    <div class="split">
      <div class="pane">
        <div class="pane-head">Ảnh gốc</div>
        <div ref="leftPane" class="pane-body" @scroll="rightPane && syncScroll(leftPane!, rightPane)">
          <div v-for="(p, i) in exam.pages" :key="p.id" class="orig-page">
            <img v-if="p.signedUrl" :src="p.signedUrl" :alt="`Trang ${i + 1}`">
            <div class="page-no">Trang {{ i + 1 }}</div>
          </div>
        </div>
      </div>
      <div class="scan-divider" />
      <div class="pane">
        <div class="pane-head">Kết quả OCR</div>
        <div ref="rightPane" class="pane-body" @scroll="leftPane && syncScroll(rightPane!, leftPane)">
          <div v-for="(p, i) in exam.pages" :key="p.id" class="ocr-card">
            <span class="ocr-page-tag">TRANG {{ i + 1 }}</span>
            <div v-if="!p.ocr_text" class="empty-note">Chưa có kết quả OCR cho trang này.</div>
            <template v-for="(block, bi) in layoutBlocks(p.ocr_text)" :key="bi">
              <div v-if="block.category === 'Title'" class="ocr-title">{{ plainText(block.text) }}</div>
              <div
                v-else-if="block.category === 'Section-header'"
                class="ocr-heading"
                :style="{ marginLeft: `${((block.level ?? 1) - 1) * 16}px` }"
              >
                {{ plainText(block.text) }}
              </div>
              <div v-else class="ocr-block">{{ plainText(block.text) }}</div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.finished-root { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 14px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-sub { font-size: 13px; color: var(--muted); }
.spacer { flex: 1; }
.split { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0; flex: 1; min-height: 0; padding: 26px; }
.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.pane-head {
  display: flex; align-items: center; gap: 9px; padding: 0 4px 12px;
  font-size: 12.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
}
.pane-body { flex: 1; overflow-y: auto; min-height: 0; }
.scan-divider { width: 34px; flex: none; position: relative; }
.scan-divider::before {
  content: ""; position: absolute; top: 34px; bottom: 8px; left: 50%; width: 1.5px;
  background: linear-gradient(var(--line), var(--accent) 50%, var(--line));
  background-size: 100% 200%; animation: scan 3.2s linear infinite;
}
@keyframes scan { 0% { background-position: 0 200%; } 100% { background-position: 0 -200%; } }
.orig-page {
  background: #fff; border: 1px solid var(--line); border-radius: 8px;
  box-shadow: var(--shadow-sm); position: relative; margin-bottom: 16px; padding: 10px;
}
.orig-page img { width: 100%; display: block; border-radius: 4px; }
.orig-page .page-no { text-align: center; font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); margin-top: 6px; }
.ocr-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: var(--shadow-sm); padding: 20px 22px; margin-bottom: 16px;
  font-size: 14px; line-height: 1.75;
}
.ocr-title { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
.ocr-heading { font-weight: 600; margin: 10px 0 4px; }
.ocr-block { margin-bottom: 8px; white-space: pre-line; }
.ocr-page-tag {
  font-family: var(--font-mono); font-size: 10.5px; color: var(--faint);
  border: 1px solid var(--line); border-radius: 5px; padding: 1px 7px;
  display: inline-block; margin-bottom: 10px; background: var(--surface-2);
}
@media (max-width: 900px) {
  .split { display: block; }
  .scan-divider { display: none; }
  .pane { margin-bottom: 20px; }
}
</style>
