<script setup lang="ts">
import { ListChecks } from '@lucide/vue'

const route = useRoute()
const examsStore = useExamsStore()
const pipeline = usePipelineStore()

// Live per-page progress for the 'processing' group only — pipeline.ts is
// already being polled by whichever /exams page is mounted alongside this
// sidebar (app.vue only renders it under /exams), so no extra fetch here.
function progressLabel(examId: string): string | null {
  const p = pipeline.byExamId(examId)
  if (!p || !p.total_pages) return null
  const pct = Math.round((p.completed_pages / p.total_pages) * 100)
  return `${pct}% (${p.completed_pages}/${p.total_pages})`
}

const groups: Array<{ key: 'pending' | 'processing' | 'finished' | 'failed', label: string, selectable: boolean }> = [
  { key: 'pending', label: 'Chờ xử lý', selectable: true },
  { key: 'processing', label: 'Đang xử lý', selectable: true },
  { key: 'finished', label: 'Hoàn thành', selectable: false },
  { key: 'failed', label: 'Lỗi', selectable: true }
]

function activeId(): string | null {
  const id = route.params.id
  return typeof id === 'string' ? id : null
}

// v-if="g.selectable" already guarantees this is only ever called for
// 'pending'/'processing'/'failed' — this cast just tells TS what that
// runtime guard already ensures.
function asSelectable(key: string): 'pending' | 'processing' | 'failed' {
  return key as 'pending' | 'processing' | 'failed'
}

const bulkActionLabel: Record<'pending' | 'processing' | 'failed', string> = {
  pending: 'Vào hàng đợi',
  processing: 'Rút khỏi hàng đợi',
  failed: 'Thử lại (về chờ xử lý)'
}

function runBulkAction() {
  const ids = [...examsStore.selectedIds]
  if (!ids.length || !examsStore.selectedGroup) return
  if (examsStore.selectedGroup === 'pending') examsStore.enqueue(ids)
  else if (examsStore.selectedGroup === 'processing') examsStore.dequeue(ids)
  else examsStore.retry(ids)
}
</script>

<template>
  <aside class="secondary" aria-label="Danh sách đề">
    <div class="sec-head">
      <div class="sec-title">Tất cả đề</div>
      <div class="sec-sub">Theo dõi trạng thái xử lý OCR</div>
    </div>
    <div class="sec-body">
      <div v-if="examsStore.offline" class="empty-note">Mất kết nối tới Processor API.</div>
      <template v-else>
        <div v-for="g in groups" :key="g.key" class="sec-group">
          <div class="sec-group-label">
            <span class="dot" :class="g.key" />
            {{ g.label }}
            <span class="count-pill">{{ examsStore.byStatus(g.key).length }}</span>
            <button
              v-if="g.selectable && examsStore.byStatus(g.key).length"
              type="button"
              class="select-all-link"
              @click="examsStore.selectAllInGroup(asSelectable(g.key))"
            >
              Chọn tất cả
            </button>
          </div>
          <div v-if="!examsStore.byStatus(g.key).length" class="empty-note">Trống</div>
          <div
            v-for="e in examsStore.byStatus(g.key)"
            :key="e.id"
            class="doc-row"
          >
            <input
              v-if="g.selectable"
              type="checkbox"
              class="doc-check"
              :checked="examsStore.selectedIds.has(e.id)"
              :aria-label="`Chọn ${e.title}`"
              @click.stop
              @change="examsStore.toggleRow(e.id, asSelectable(g.key))"
            >
            <NuxtLink :to="`/exams/${e.id}`" class="doc-item" :class="{ active: activeId() === e.id }">
              <span class="doc-meta">
                <span class="doc-name">{{ e.title }}</span>
                <span v-if="g.key === 'processing' && progressLabel(e.id)" class="doc-info doc-progress">{{ progressLabel(e.id) }}</span>
                <span v-if="g.key === 'failed' && e.error_message" class="doc-info doc-error">{{ e.error_message }}</span>
              </span>
            </NuxtLink>
          </div>
        </div>
      </template>
    </div>

    <div v-if="examsStore.selectedCount" class="bulk-bar">
      <ListChecks :size="16" class="bulk-icon" />
      <span class="bulk-count">{{ examsStore.selectedCount }} đã chọn</span>
      <span class="spacer" />
      <button type="button" class="bulk-clear" @click="examsStore.clearSelection()">Bỏ chọn</button>
      <button type="button" class="btn btn-primary bulk-action" @click="runBulkAction">
        {{ examsStore.selectedGroup ? bulkActionLabel[examsStore.selectedGroup] : '' }}
      </button>
    </div>
  </aside>
</template>

<style scoped>
.secondary {
  width: 280px; flex: none; background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex; flex-direction: column; overflow: hidden;
}
.sec-head { padding: 18px 18px 10px; }
.sec-title { font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
.sec-sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.sec-body { flex: 1; overflow-y: auto; padding: 6px 10px 16px; }
.sec-group { margin-top: 10px; }
.sec-group-label {
  display: flex; align-items: center; gap: 7px;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); padding: 6px 8px;
}
.select-all-link {
  margin-left: auto; font-size: 10.5px; font-weight: 600; text-transform: none;
  letter-spacing: normal; color: var(--accent); padding: 2px 4px; border-radius: 5px;
}
.select-all-link:hover { background: var(--accent-soft); }

.doc-row { display: flex; align-items: center; gap: 4px; }
.doc-check {
  flex: none; width: 15px; height: 15px; margin-left: 6px; accent-color: var(--accent);
  cursor: pointer;
}
.doc-item {
  flex: 1; min-width: 0; text-align: left; display: flex; gap: 10px; align-items: center;
  padding: 9px 8px; border-radius: 8px; transition: background .12s;
  text-decoration: none; color: inherit;
}
.doc-item:hover { background: var(--surface-2); }
.doc-item.active { background: var(--accent-soft); }
.doc-item.active .doc-name { color: var(--accent-ink); }
.doc-meta { min-width: 0; flex: 1; }
.doc-name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
.doc-info { font-family: var(--font-mono); font-size: 11px; color: var(--faint); margin-top: 1px; display: block; }
.doc-error { font-family: var(--font-body); color: var(--danger); white-space: normal; }
.doc-progress { color: var(--accent-ink); }

.bulk-bar {
  flex: none; display: flex; align-items: center; gap: 8px;
  padding: 12px 14px; border-top: 1px solid var(--line); background: var(--surface-2);
}
.bulk-icon { color: var(--accent); flex: none; }
.bulk-count { font-size: 12.5px; font-weight: 600; color: var(--ink); white-space: nowrap; }
.spacer { flex: 1; }
.bulk-clear { font-size: 12px; color: var(--muted); padding: 4px 6px; }
.bulk-clear:hover { color: var(--ink); }
.bulk-action { min-height: 32px; padding: 6px 14px; font-size: 12.5px; }
</style>
