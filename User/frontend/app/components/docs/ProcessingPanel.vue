<script setup lang="ts">
import { Info, Trash2 } from '@lucide/vue'
import type { PageGridItem } from '~/components/shared/PageGrid.vue'

const props = defineProps<{ exam: NonNullable<ReturnType<typeof useDocumentsStore>['activeExam']> }>()

const gridItems = computed<PageGridItem[]>(() =>
  props.exam.pages.map(p => ({ id: p.id, source: p.file_path.split('/').pop() ?? '', imageUrl: p.signedUrl }))
)

function deleteExam() {
  useDocumentsStore().deleteExam(props.exam.id, props.exam.status)
}
</script>

<template>
  <div>
    <div class="main-head">
      <div>
        <div class="main-title">{{ exam.title }}</div>
        <div class="main-sub">{{ exam.pages.length }} trang</div>
      </div>
      <span class="spacer" />
      <button class="btn btn-ghost btn-icon" title="Xóa đề này" aria-label="Xóa đề" @click="deleteExam">
        <Trash2 :size="16" />
      </button>
      <StatusBadge status="processing" />
    </div>
    <div class="main-body">
      <div class="note-strip">
        <Info :size="15" />
        Tài liệu đang được xử lý — chỉ có thể xem, không thể chỉnh sửa.
      </div>
      <PageGrid :items="gridItems" />
    </div>
  </div>
</template>

<style scoped>
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 14px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-sub { font-size: 13px; color: var(--muted); }
.main-body { padding: 26px; }
.spacer { flex: 1; }
.note-strip {
  display: flex; align-items: center; gap: 9px; font-size: 13.5px; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line); border-radius: 9px;
  padding: 10px 15px; margin-bottom: 16px;
}
.note-strip svg { color: var(--processing); flex: none; }
</style>
