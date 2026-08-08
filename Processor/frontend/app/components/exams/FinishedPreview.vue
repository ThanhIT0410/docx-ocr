<script setup lang="ts">
import type { DocumentLayout, ExamDetail, OcrPageResult } from '~/types/models'

/** Read-only port of User/frontend's `docs/FinishedPanel.vue` — same
 * split-view + Text/Layout tabs, minus the "Xuất kết quả" button and
 * everything sidecar/export-related, since Processor never exports (that's
 * User's job, via its local FastAPI sidecar Processor has no access to).
 * Kept as its own component (not shared across the two Nuxt projects —
 * they're separate apps with separate dependency trees) but intentionally
 * mirrors the original closely so the two stay easy to compare/keep in sync. */
const props = defineProps<{
  exam: ExamDetail
  previewUrls: Record<string, string>
}>()

const leftPane = ref<HTMLElement>()
const rightPane = ref<HTMLElement>()

const activeTab = ref<'text' | 'layout'>('text')
const hoveredKey = ref<string | null>(null)
function blockKey(pageId: string, blockIndex: number): string {
  return `${pageId}:${blockIndex}`
}

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

function bboxStyle(block: DocumentLayout, page: OcrPageResult) {
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

function plainText(text: string): string {
  return new DOMParser().parseFromString(text, 'text/html').body.textContent ?? ''
}

function layoutBlocks(raw: OcrPageResult | null): DocumentLayout[] {
  return raw?.layouts ?? []
}

function boxedLayoutBlocks(raw: OcrPageResult | null): DocumentLayout[] {
  return layoutBlocks(raw).filter(b => b.bbox?.length === 4)
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
  <div class="split">
    <div class="pane">
      <div class="pane-head">Ảnh gốc</div>
      <div ref="leftPane" class="pane-body" @scroll="rightPane && syncScroll(leftPane!, rightPane)">
        <div v-for="(p, i) in exam.pages" :key="p.id" class="orig-page">
          <div class="orig-page-imgwrap">
            <img v-if="previewUrls[p.id]" :src="previewUrls[p.id]" :alt="`Trang ${i + 1}`">
            <template v-if="activeTab === 'layout' && p.ocr_text">
              <div
                v-for="(block, bi) in boxedLayoutBlocks(p.ocr_text)"
                :key="bi"
                class="bbox-box"
                :class="{ active: hoveredKey === blockKey(p.id, bi) }"
                :style="bboxStyle(block, p.ocr_text)"
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
</template>

<style scoped>
.split { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0; flex: 1; min-height: 0; }
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
