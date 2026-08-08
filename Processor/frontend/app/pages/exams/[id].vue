<script setup lang="ts">
import type { LightboxItem } from '~/components/shared/PageLightbox.vue'
import type { PageRecord } from '~/types/models'

const route = useRoute()
const store = useExamsStore()
const pipeline = usePipelineStore()

const id = computed(() => String(route.params.id))

watch(id, (value) => store.openExam(value), { immediate: true })

// Separate from the exam-detail poll on purpose (see
// useProcessorApi.ts::getExamPreview's doc comment) — fetched once per
// exam opened, not on every 3s re-poll. Silently left empty on failure
// (e.g. Storage object missing) — a missing thumbnail just means "no
// preview available", not something worth an error toast over.
const previewUrls = ref<Record<string, string>>({})
watch(id, async (examId) => {
  previewUrls.value = {}
  try {
    const res = await useProcessorApi().getExamPreview(examId)
    const map: Record<string, string> = {}
    for (const p of res.pages) if (p.url) map[p.page_id] = p.url
    previewUrls.value = map
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[exams/[id]] getExamPreview failed', err)
  }
}, { immediate: true })

onMounted(() => {
  store.startListPolling()
  pipeline.startPolling()
})
onBeforeUnmount(() => {
  store.stopListPolling()
  pipeline.stopPolling()
  store.stopDetailPolling()
})

const liveProgress = computed(() => pipeline.byExamId(id.value))

function pageSummary(page: PageRecord): string {
  if (!page.ocr_text) return 'Chưa xử lý'
  const layouts = page.ocr_text.layouts
  const counts = new Map<string, number>()
  for (const l of layouts) counts.set(l.category, (counts.get(l.category) ?? 0) + 1)
  const parts = Array.from(counts.entries()).map(([cat, n]) => `${cat}×${n}`)
  return `${layouts.length} khối — ${parts.join(', ')}`
}

/** Click-to-zoom for the page-list thumbnails (pending/processing/failed —
 * 'finished' uses `FinishedPreview` below instead, which has its own
 * larger scrollable panes and doesn't need this). Replaces what used to be
 * a plain `<a target="_blank">` opening the image in a new browser tab —
 * awkward inside Electron and gives no way to page through the exam while
 * zoomed in. */
const lightboxIndex = ref<number | null>(null)
const lightboxItems = computed<LightboxItem[]>(() =>
  (store.activeExam?.pages ?? []).map(p => ({ id: p.id, url: previewUrls.value[p.id] ?? null, order: p.page_order }))
)
</script>

<template>
  <main class="main">
    <template v-if="store.activeExam && store.activeExam.id === id">
      <div class="main-head">
        <div>
          <div class="main-title">{{ store.activeExam.title }}</div>
          <div class="main-sub">
            {{ store.activeExam.pages.length }} trang · gửi lúc {{ formatDateTime(store.activeExam.uploaded_at) }}
          </div>
        </div>
        <span class="spacer" />
        <PipelineStatusControl />
        <StatusBadge :status="store.activeExam.status" />
      </div>

      <div class="main-body">
        <div v-if="store.activeExam.status === 'processing'" class="info-block">
          <template v-if="liveProgress">
            <div class="progress-row">
              <div class="progress-track">
                <div
                  class="progress-fill"
                  :style="{ width: (liveProgress.total_pages ? liveProgress.completed_pages / liveProgress.total_pages * 100 : 0) + '%' }"
                />
              </div>
              <span class="progress-label">{{ liveProgress.completed_pages }}/{{ liveProgress.total_pages }} trang</span>
            </div>
          </template>
          <div v-else class="hint">Đang chờ trong hàng đợi OCR — chưa tới lượt xử lý.</div>
        </div>

        <div v-if="store.activeExam.status === 'failed'" class="error-block">
          <div class="error-title">Lỗi xử lý</div>
          <div class="error-message">{{ store.activeExam.error_message || '(không có thông điệp lỗi)' }}</div>
          <div class="hint error-hint">Chưa hỗ trợ xử lý lại tự động — cần thao tác thủ công phía vận hành.</div>
        </div>

        <div class="action-row">
          <button
            v-if="store.activeExam.status === 'pending'"
            class="btn btn-primary"
            @click="store.enqueue([store.activeExam.id])"
          >
            Đưa vào hàng đợi
          </button>
          <button
            v-if="store.activeExam.status === 'processing'"
            class="btn btn-ghost"
            @click="store.dequeue([store.activeExam.id])"
          >
            Rút khỏi hàng đợi
          </button>
        </div>

        <FinishedPreview
          v-if="store.activeExam.status === 'finished'"
          :exam="store.activeExam"
          :preview-urls="previewUrls"
        />
        <template v-else>
          <div class="pages-head">Danh sách trang</div>
          <div class="pages-list">
            <div v-for="(p, i) in store.activeExam.pages" :key="p.id" class="page-row">
              <button
                v-if="previewUrls[p.id]"
                type="button"
                class="page-thumb-btn"
                title="Bấm để xem ảnh lớn"
                @click="lightboxIndex = i"
              >
                <img :src="previewUrls[p.id]" class="page-thumb" alt="">
              </button>
              <div v-else class="page-thumb page-thumb-empty" aria-hidden="true" />
              <span class="page-order">#{{ p.page_order }}</span>
              <span class="page-summary" :class="{ done: !!p.ocr_text }">{{ pageSummary(p) }}</span>
            </div>
          </div>
        </template>
      </div>

      <PageLightbox
        v-if="lightboxIndex !== null"
        :items="lightboxItems"
        :index="lightboxIndex"
        @update:index="lightboxIndex = $event"
        @close="lightboxIndex = null"
      />
    </template>
    <div v-else-if="store.loadingActive" class="main-body">
      <div class="empty-note" style="padding-top:60px">Đang tải…</div>
    </div>
    <div v-else class="main-body">
      <div class="empty-note" style="padding-top:60px">Không tìm thấy đề này.</div>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 14px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-sub { font-size: 13px; color: var(--muted); }
.main-body { flex: 1; display: flex; flex-direction: column; min-height: 0; overflow-y: auto; padding: 26px; }
.spacer { flex: 1; }

.info-block { margin-bottom: 18px; }
.progress-row { display: flex; align-items: center; gap: 10px; max-width: 420px; }
.progress-label { font-family: var(--font-mono); font-size: 12.5px; color: var(--muted); flex: none; }

.error-block {
  background: var(--danger-bg); border: 1px solid var(--danger);
  border-radius: var(--radius); padding: 14px 16px; margin-bottom: 18px;
}
.error-title { font-size: 13px; font-weight: 700; color: var(--danger); margin-bottom: 4px; }
.error-message { font-family: var(--font-mono); font-size: 13px; color: var(--ink); white-space: pre-wrap; word-break: break-word; }
.error-hint { margin-top: 8px; }

.action-row { display: flex; gap: 10px; }

.pages-head { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 22px 0 8px; }
.pages-list { display: flex; flex-direction: column; border-top: 1px solid var(--line); }
.page-row { display: flex; gap: 14px; align-items: center; padding: 9px 4px; border-bottom: 1px solid var(--line); }
.page-thumb-btn { flex: none; display: block; padding: 0; border-radius: 6px; cursor: zoom-in; }
.page-thumb {
  width: 44px; height: 58px; border-radius: 6px; border: 1px solid var(--line);
  object-fit: cover; background: var(--surface-2); flex: none; display: block;
}
.page-thumb-empty { display: block; }
.page-order { font-family: var(--font-mono); font-size: 12.5px; color: var(--faint); flex: none; width: 40px; }
.page-summary { font-size: 13.5px; color: var(--muted); }
.page-summary.done { color: var(--ink); }
</style>
