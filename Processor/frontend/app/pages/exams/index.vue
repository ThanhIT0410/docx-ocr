<script setup lang="ts">
import { AlertTriangle } from '@lucide/vue'

const examsStore = useExamsStore()
const pipeline = usePipelineStore()

onMounted(() => {
  examsStore.startListPolling()
  pipeline.startPolling()
})
onBeforeUnmount(() => {
  examsStore.stopListPolling()
  pipeline.stopPolling()
})

const hasAnyExam = computed(() =>
  !!(examsStore.pending.length || examsStore.processing.length || examsStore.finished.length || examsStore.failed.length)
)

function pageRatio(completed: number, total: number): number {
  return total ? Math.round((completed / total) * 100) : 0
}
</script>

<template>
  <main class="main">
    <div class="main-head">
      <div class="main-title">Tất cả đề</div>
      <span class="spacer" />
      <PipelineStatusControl />
    </div>

    <div class="main-body">
      <div v-if="pipeline.error" class="pipeline-error-note">
        <AlertTriangle :size="15" />
        <span>{{ pipeline.error }}</span>
      </div>

      <template v-if="pipeline.items.length">
        <div class="section-head">Đang xử lý ({{ pipeline.items.length }})</div>
        <div class="progress-list">
          <NuxtLink
            v-for="item in pipeline.items"
            :key="item.exam_id"
            :to="`/exams/${item.exam_id}`"
            class="progress-row"
          >
            <span class="progress-title">{{ item.exam_title }}</span>
            <div class="progress-track prow-track">
              <div class="progress-fill" :style="{ width: pageRatio(item.completed_pages, item.total_pages) + '%' }" />
            </div>
            <span class="progress-count">{{ item.completed_pages }}/{{ item.total_pages }}</span>
          </NuxtLink>
        </div>
      </template>

      <div v-else class="empty-note" style="padding-top:60px">
        {{ hasAnyExam
          ? 'Chọn một đề ở thanh bên để xem chi tiết, hoặc tick chọn nhiều đề để đưa vào hàng đợi.'
          : 'Chưa có đề nào — chờ User submit đề mới.' }}
      </div>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 12px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.spacer { flex: 1; }
.main-body { flex: 1; overflow-y: auto; padding: 26px; }

.pipeline-error-note {
  display: flex; align-items: flex-start; gap: 9px; font-size: 13px; color: var(--danger);
  background: var(--danger-bg); border: 1px solid var(--danger); border-radius: var(--radius);
  padding: 12px 14px; margin-bottom: 20px;
}
.pipeline-error-note svg { flex: none; margin-top: 1px; }

.section-head {
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); margin-bottom: 10px;
}
.progress-list { display: flex; flex-direction: column; border-top: 1px solid var(--line); }
.progress-row {
  display: flex; align-items: center; gap: 14px; padding: 12px 4px; border-bottom: 1px solid var(--line);
  text-decoration: none; color: inherit;
}
.progress-row:hover { background: var(--surface-2); }
.progress-title { font-size: 13.5px; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.prow-track { width: 160px; flex: none; }
.progress-count { font-family: var(--font-mono); font-size: 12px; color: var(--muted); flex: none; width: 48px; text-align: right; }
</style>
