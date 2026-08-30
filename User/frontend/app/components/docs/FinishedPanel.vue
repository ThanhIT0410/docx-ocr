<script setup lang="ts">
import { ChevronDown, Download, FileImage, FileText, Trash2 } from '@lucide/vue'
import type { DocumentLayout, OcrPageResult } from '~/types/models'

const props = defineProps<{ exam: NonNullable<ReturnType<typeof useDocumentsStore>['activeExam']> }>()

function deleteExam() {
  useDocumentsStore().deleteExam(props.exam.id, props.exam.status)
}

const exporting = ref(false)
const leftPane = ref<HTMLElement>()
const rightPane = ref<HTMLElement>()

const showExportMenu = ref(false)
const exportMenuRoot = ref<HTMLElement>()
const EXPORT_MODES = [
  {
    mode: 'layout' as const,
    label: 'DOCX, giữ layout',
    desc: 'File Word — bảng, cột, tiêu đề sắp xếp giống trang gốc',
    icon: FileText
  },
  {
    mode: 'plain' as const,
    label: 'DOCX, text thuần',
    desc: 'File Word — chỉ có chữ, nối theo thứ tự, không giữ bố cục',
    icon: FileText
  },
  {
    mode: 'pdf' as const,
    label: 'PDF',
    desc: 'Ghép ảnh gốc các trang lại thành 1 file PDF, không nhận dạng chữ',
    icon: FileImage
  }
]

function onDocumentClick(e: MouseEvent) {
  if (exportMenuRoot.value && !exportMenuRoot.value.contains(e.target as Node)) {
    showExportMenu.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onDocumentClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocumentClick))

const activeTab = ref<'text' | 'layout'>('text')
/** `${pageId}:${blockIndex}` of the block currently hovered on either side —
 * drives the highlight that links a bbox on the left to its list entry on
 * the right (and vice versa), so the two views read as one thing. */
const hoveredKey = ref<string | null>(null)
function blockKey(pageId: string, blockIndex: number): string {
  return `${pageId}:${blockIndex}`
}

/** One color per Processor category (Processor/backend/app/prompts.py's
 * `OCR_PROMPT` label list) — kept here rather than in a shared file since
 * this is the only place that renders layout blocks visually. */
const CATEGORY_COLORS: Record<string, string> = {
  'Title': '#d6336c',
  'Section-header': '#862e9c',
  'Text': '#2b4fd8',
  'Page-header': '#c2255c',
  'Page-footer': '#a61e4d',
  'List-item': '#12855f',
  'Table': '#e8590c',
  'Caption': '#495057',
  'Footnote': '#868e96',
  'Formula': '#1098ad',
  'Picture': '#f08c00'
}
function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? '#495057'
}

/** `bbox` is `[x1, y1, x2, y2]` in the model's **input** pixel space (see
 * `DocumentLayout` in ~/types/models.ts), so it's mapped to the displayed
 * image with percentages of `input_width/height` — that stays correct
 * regardless of how large the browser actually renders the image. */
function bboxStyle(block: DocumentLayout, page: OcrPageResult) {
  // Callers only ever pass blocks already filtered by `boxedLayoutBlocks`
  // (bbox.length === 4), TS just can't see that through the v-for.
  const [x1, y1, x2, y2] = block.bbox as [number, number, number, number]
  const color = categoryColor(block.category)
  return {
    left: `${(x1 / page.input_width) * 100}%`,
    top: `${(y1 / page.input_height) * 100}%`,
    width: `${((x2 - x1) / page.input_width) * 100}%`,
    height: `${((y2 - y1) / page.input_height) * 100}%`,
    borderColor: color,
    background: `${color}26`
  }
}

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

/** Same as `layoutBlocks`, but drops blocks with a malformed bbox (can be
 * `[]` — see Processor's `_parse_bbox` fallback) since those can't be drawn
 * as an overlay rectangle or reliably mapped back to a list entry. */
function boxedLayoutBlocks(raw: unknown): DocumentLayout[] {
  return layoutBlocks(raw).filter(b => b.bbox?.length === 4)
}

async function exportResult(mode: 'layout' | 'plain' | 'pdf') {
  showExportMenu.value = false
  exporting.value = true
  useToast().info('Đang xuất kết quả OCR…')
  try {
    const { blob, filename } = await useSidecar().exportExam({
      examId: props.exam.id,
      title: props.exam.title,
      mode,
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
      <button class="btn btn-ghost btn-icon" title="Xóa đề này" aria-label="Xóa đề" @click="deleteExam">
        <Trash2 :size="16" />
      </button>
      <StatusBadge status="finished" />
      <div ref="exportMenuRoot" class="export-menu-wrap">
        <button
          class="btn btn-primary"
          title="Tải kết quả nhận dạng chữ (OCR) về máy của bạn"
          :disabled="exporting"
          @click="showExportMenu = !showExportMenu"
        >
          <Download :size="17" /> {{ exporting ? 'Đang xuất…' : 'Xuất kết quả' }}
          <ChevronDown :size="15" />
        </button>
        <div v-if="showExportMenu" class="export-menu">
          <button
            v-for="opt in EXPORT_MODES"
            :key="opt.mode"
            type="button"
            class="export-menu-item"
            :title="opt.desc"
            @click="exportResult(opt.mode)"
          >
            <component :is="opt.icon" :size="20" class="export-menu-icon" />
            <span class="export-menu-text">
              <span class="export-menu-label">{{ opt.label }}</span>
              <span class="export-menu-desc">{{ opt.desc }}</span>
            </span>
          </button>
        </div>
      </div>
    </div>

    <div class="split">
      <div class="pane">
        <div class="pane-head">Ảnh gốc</div>
        <div ref="leftPane" class="pane-body" @scroll="rightPane && syncScroll(leftPane!, rightPane)">
          <div v-for="(p, i) in exam.pages" :key="p.id" class="orig-page">
            <div class="orig-page-imgwrap">
              <img v-if="p.signedUrl" :src="p.signedUrl" :alt="`Trang ${i + 1}`">
              <template v-if="activeTab === 'layout' && p.ocr_text">
                <div
                  v-for="(block, bi) in boxedLayoutBlocks(p.ocr_text)"
                  :key="bi"
                  class="bbox-box"
                  :class="{ active: hoveredKey === blockKey(p.id, bi) }"
                  :style="bboxStyle(block, p.ocr_text!)"
                  :title="block.category"
                  @mouseenter="hoveredKey = blockKey(p.id, bi)"
                  @mouseleave="hoveredKey = null"
                />
              </template>
            </div>
            <div class="page-no">Trang {{ i + 1 }}</div>
          </div>
        </div>
      </div>
      <div class="scan-divider" />
      <div class="pane">
        <div class="pane-head-col">
          <div class="pane-head">Kết quả OCR</div>
          <div class="tabs-row">
            <button type="button" class="tab-btn" :class="{ active: activeTab === 'text' }" @click="activeTab = 'text'">
              Text
            </button>
            <button type="button" class="tab-btn" :class="{ active: activeTab === 'layout' }" @click="activeTab = 'layout'">
              Layout
            </button>
          </div>
        </div>
        <div ref="rightPane" class="pane-body" @scroll="leftPane && syncScroll(rightPane!, leftPane)">
          <div v-for="(p, i) in exam.pages" :key="p.id" class="ocr-card">
            <span class="ocr-page-tag">TRANG {{ i + 1 }}</span>
            <div v-if="!p.ocr_text" class="empty-note">Chưa có kết quả OCR cho trang này.</div>
            <template v-else-if="activeTab === 'text'">
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
            </template>
            <template v-else>
              <div
                v-for="(block, bi) in boxedLayoutBlocks(p.ocr_text)"
                :key="bi"
                class="layout-block"
                :class="{ active: hoveredKey === blockKey(p.id, bi) }"
                :style="{ borderLeftColor: categoryColor(block.category) }"
                @mouseenter="hoveredKey = blockKey(p.id, bi)"
                @mouseleave="hoveredKey = null"
              >
                <span class="layout-block-label" :style="{ color: categoryColor(block.category) }">{{ block.category.toUpperCase() }}</span>
                <div class="layout-block-text">{{ plainText(block.text) }}</div>
              </div>
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
.export-menu-wrap { position: relative; }
.export-menu {
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 20; min-width: 280px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: var(--shadow-md, var(--shadow-sm)); padding: 6px; display: flex; flex-direction: column; gap: 2px;
}
.export-menu-item {
  display: flex; align-items: center; gap: 12px;
  text-align: left; border: none; background: transparent; border-radius: 6px;
  padding: 10px; cursor: pointer; transition: background .15s;
}
.export-menu-item:hover { background: var(--surface-2); }
.export-menu-icon { flex: none; color: var(--accent); }
.export-menu-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.export-menu-label { font-size: 14px; font-weight: 600; color: var(--ink); }
.export-menu-desc { font-size: 12px; color: var(--muted); line-height: 1.4; }
.split { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0; flex: 1; min-height: 0; padding: 26px; }
.pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.pane-head {
  display: flex; align-items: center; gap: 9px; padding: 0 4px 12px;
  font-size: 12.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
}
.pane-body { flex: 1; overflow-y: auto; min-height: 0; }
.pane-head-col { display: flex; flex-direction: column; gap: 8px; padding-bottom: 12px; }
.pane-head-col .pane-head { padding-bottom: 0; }
.tabs-row { display: flex; gap: 4px; padding: 0 4px; }
.tab-btn {
  border: 1px solid var(--line); background: var(--surface); color: var(--muted);
  font-size: 12.5px; font-weight: 600; padding: 5px 14px; border-radius: 6px;
  cursor: pointer; transition: all .15s;
}
.tab-btn:hover { color: var(--ink); }
.tab-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.orig-page-imgwrap { position: relative; }
.bbox-box {
  position: absolute; border: 1.5px solid; border-radius: 2px; cursor: pointer;
  transition: background .15s, box-shadow .15s;
}
.bbox-box.active { box-shadow: 0 0 0 2px rgba(0, 0, 0, .15) inset; background: rgba(0, 0, 0, .12) !important; }
.layout-block {
  border: 1px solid var(--line); border-left-width: 4px; border-radius: 6px;
  padding: 8px 12px; margin-bottom: 10px; cursor: pointer; transition: background .15s;
}
.layout-block:last-child { margin-bottom: 0; }
.layout-block.active, .layout-block:hover { background: var(--surface-2); }
.layout-block-label {
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 700; letter-spacing: .04em;
  display: block; margin-bottom: 4px;
}
.layout-block-text { font-size: 13.5px; line-height: 1.6; white-space: pre-line; }
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
