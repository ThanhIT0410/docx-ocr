<script setup lang="ts">
import { Play } from '@lucide/vue'

// Reads pipeline.ts's already-running poll (started by whichever page
// mounted first — exams/index.vue and exams/[id].vue both call
// pipeline.startPolling()) rather than starting its own, so this can be
// dropped into any page's header without double-polling.
const pipeline = usePipelineStore()
const examsStore = useExamsStore()

// Anything with status='processing' is currently sitting in the backend's
// in-memory queue (see queue_service.py) UNLESS the pipeline is actively
// draining it right now — while idle, that status can only mean "queued,
// not yet picked up" (nothing else could have put it there). Used purely
// to decide whether the start button should be clickable at all; the
// backend enforces the real 409 (see controllers/ocr.py) regardless.
const hasQueuedWork = computed(() => examsStore.processing.length > 0)

// A "run" (POST /processor/ocr/start) drains whatever's in the queue
// right now, then the background task ends on its own — pipeline.running
// goes back to false with no error, same as "never started". So 'idle'
// here covers both "never run yet" and "finished a run, nothing queued" —
// there's no meaningful difference for the operator: either way, the next
// step is enqueue more + click the button again.
const pillState = computed(() => {
  if (pipeline.running) return 'running'
  return pipeline.error ? 'error' : 'idle'
})
const pillLabel = computed(() => {
  if (pipeline.running) return 'Đang chạy OCR'
  return pipeline.error ? 'Đã dừng — có lỗi' : 'Sẵn sàng — bấm để chạy'
})
</script>

<template>
  <span class="pipeline-pill" :class="pillState" :title="pipeline.error ?? undefined">
    <span class="pipeline-dot" :class="pillState" />
    {{ pillLabel }}
  </span>
  <button
    class="btn btn-primary"
    :disabled="pipeline.starting || pipeline.running || !hasQueuedWork"
    :title="!hasQueuedWork && !pipeline.running ? 'Chưa có đề nào trong hàng đợi — đưa đề vào hàng đợi trước' : undefined"
    @click="pipeline.start()"
  >
    <Play :size="16" />
    {{ pipeline.starting ? 'Đang bật…' : 'Bật xử lý OCR' }}
  </button>
</template>

<style scoped>
.pipeline-pill {
  display: inline-flex; align-items: center; gap: 7px;
  font-size: 13px; font-weight: 600; border-radius: 99px; padding: 5px 13px;
  white-space: nowrap;
}
.pipeline-pill.idle { color: var(--muted); background: var(--surface-2); }
.pipeline-pill.running { color: var(--processing); background: var(--processing-bg); }
.pipeline-pill.error { color: var(--danger); background: var(--danger-bg); }
.pipeline-dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.pipeline-dot.idle { background: var(--faint); }
.pipeline-dot.running { background: var(--processing); animation: pulse 1.6s ease-in-out infinite; }
.pipeline-dot.error { background: var(--danger); }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
</style>
