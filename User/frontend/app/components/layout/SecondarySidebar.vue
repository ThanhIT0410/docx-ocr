<script setup lang="ts">
import type { ExamListItem, SortField } from '~/stores/documents'

const route = useRoute()
const docsStore = useDocumentsStore()
const isUpload = computed(() => route.path.startsWith('/upload'))

// 'processing' has no group here on purpose — an exam Processor has
// claimed isn't shown anywhere in this list until it finishes (see
// stores/documents.ts's ListedStatus doc comment).
const groups: Array<{ key: 'pending' | 'finished', label: string }> = [
  { key: 'pending', label: 'Chờ xử lý' },
  { key: 'finished', label: 'Hoàn thành' }
]

const SORT_OPTIONS: Array<{ field: SortField, label: string }> = [
  { field: 'uploaded_at', label: 'Ngày gửi' },
  { field: 'finished_at', label: 'Ngày hoàn thành' }
]

function activeId(): string | null {
  const id = route.params.id
  return typeof id === 'string' ? id : null
}

/** Shows whichever date the current sort is actually ordering by, so the
 * effect of switching sort is visible right on each row — not just a
 * silent reorder the user has to take on faith. Pending exams have no
 * `finished_at` yet (see byStatus's doc comment), so they always show the
 * send date regardless of the chosen sort. */
function rowDateLabel(d: ExamListItem): string {
  if (d.status === 'finished' && docsStore.sortBy === 'finished_at' && d.finished_at) {
    return `Xong ${formatRelativeTime(d.finished_at)}`
  }
  return `Gửi ${formatRelativeTime(d.uploaded_at)}`
}
</script>

<template>
  <aside class="secondary" aria-label="Danh sách phụ">
    <template v-if="isUpload">
      <div class="sec-head">
        <div class="sec-title">Tải đề lên</div>
        <div class="sec-sub">Đã gửi gần đây</div>
      </div>
      <div class="sec-body">
        <div v-if="!docsStore.recent.length" class="empty-note">Chưa có đề nào được gửi.</div>
        <NuxtLink
          v-for="r in docsStore.recent"
          :key="r.id"
          :to="`/documents/${r.id}`"
          class="doc-item"
        >
          <span class="doc-thumb" aria-hidden="true" />
          <span class="doc-meta">
            <span class="doc-name">{{ r.title }}</span>
            <span class="doc-info">{{ r.pageCount }} trang · {{ formatRelativeTime(r.uploaded_at) }}</span>
          </span>
        </NuxtLink>
      </div>
    </template>

    <template v-else>
      <div class="sec-head">
        <div class="sec-title">Tất cả tài liệu</div>
        <div class="sec-sub">Theo dõi trạng thái xử lý</div>
        <div class="sort-row">
          <span class="sort-label">Sắp xếp theo</span>
          <div class="sort-tabs">
            <button
              v-for="opt in SORT_OPTIONS"
              :key="opt.field"
              type="button"
              class="sort-tab"
              :class="{ active: docsStore.sortBy === opt.field }"
              :title="`Xếp đề theo ${opt.label.toLowerCase()}, mới nhất lên đầu`"
              @click="docsStore.setSortBy(opt.field)"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
      </div>
      <div class="sec-body">
        <div v-for="g in groups" :key="g.key" class="sec-group">
          <div class="sec-group-label">
            <span class="dot" :class="g.key" />
            {{ g.label }}
            <span class="count-pill">{{ docsStore.byStatus(g.key).length }}</span>
          </div>
          <div v-if="!docsStore.byStatus(g.key).length" class="empty-note">Trống</div>
          <NuxtLink
            v-for="d in docsStore.byStatus(g.key)"
            :key="d.id"
            :to="`/documents/${d.id}`"
            class="doc-item"
            :class="{ active: activeId() === d.id }"
          >
            <span class="doc-thumb" aria-hidden="true" />
            <span class="doc-meta">
              <span class="doc-name">{{ d.title }}</span>
              <span class="doc-info">{{ d.pageCount }} trang · {{ rowDateLabel(d) }}</span>
            </span>
          </NuxtLink>
        </div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.secondary {
  width: 264px; flex: none; background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex; flex-direction: column; overflow: hidden;
}
.sec-head { padding: 18px 18px 10px; }
.sec-title { font-size: 15px; font-weight: 700; letter-spacing: -.01em; }
.sec-sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.sort-row { margin-top: 12px; }
.sort-label { display: block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--faint); margin-bottom: 6px; }
.sort-tabs { display: flex; gap: 4px; }
.sort-tab {
  flex: 1; border: 1px solid var(--line); background: var(--surface); color: var(--muted);
  font-size: 12px; font-weight: 600; padding: 6px 8px; border-radius: 6px;
  cursor: pointer; transition: all .15s; text-align: center;
}
.sort-tab:hover { color: var(--ink); }
.sort-tab.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.sec-body { flex: 1; overflow-y: auto; padding: 6px 10px 16px; }
.sec-group { margin-top: 10px; }
.sec-group-label {
  display: flex; align-items: center; gap: 7px;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); padding: 6px 8px;
}
.doc-item {
  width: 100%; text-align: left; display: flex; gap: 10px; align-items: center;
  padding: 9px 8px; border-radius: 8px; transition: background .12s;
  text-decoration: none; color: inherit;
}
.doc-item:hover { background: var(--surface-2); }
.doc-item.active { background: var(--accent-soft); }
.doc-item.active .doc-name { color: var(--accent-ink); }
.doc-thumb {
  width: 30px; height: 38px; flex: none; border-radius: 4px; background: #fff;
  border: 1px solid var(--line); position: relative; overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.doc-thumb::before { content: ""; position: absolute; inset: 6px 5px auto 5px; height: 2.5px; background: #C9D2E0; border-radius: 2px; }
.doc-thumb::after {
  content: ""; position: absolute; inset: 12px 5px auto 5px; height: 20px;
  background: repeating-linear-gradient(#E4E9F1 0 2px, transparent 2px 6px);
}
.doc-meta { min-width: 0; flex: 1; }
.doc-name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
.doc-info { font-family: var(--font-mono); font-size: 11px; color: var(--faint); margin-top: 1px; display: block; }
</style>
