<script setup lang="ts">
import { Move, Trash2 } from '@lucide/vue'
import type { PageGridItem } from '~/components/shared/PageGrid.vue'

const props = defineProps<{ exam: NonNullable<ReturnType<typeof useDocumentsStore>['activeExam']> }>()

function deleteExam() {
  useDocumentsStore().deleteExam(props.exam.id, props.exam.status)
}

const draft = ref(props.exam.pages.slice())
const deleted = ref<typeof props.exam.pages>([])
const dirty = ref(false)

watch(() => props.exam.id, () => {
  draft.value = props.exam.pages.slice()
  deleted.value = []
  dirty.value = false
}, { flush: 'post' })

const gridItems = computed<PageGridItem[]>(() =>
  draft.value.map(p => ({ id: p.id, source: p.file_path.split('/').pop() ?? '', imageUrl: p.signedUrl }))
)

function onReorder(orderedIds: string[]) {
  const byId = new Map(draft.value.map(p => [p.id, p]))
  draft.value = orderedIds.map(id => byId.get(id)!).filter(Boolean)
  dirty.value = true
}

function onDelete(id: string) {
  const idx = draft.value.findIndex(p => p.id === id)
  if (idx === -1) return
  const [removed] = draft.value.splice(idx, 1)
  if (!removed) return
  deleted.value.push(removed)
  dirty.value = true
}

async function save() {
  await useDocumentsStore().savePendingChanges(props.exam.id, draft.value.map(p => p.id), deleted.value)
  deleted.value = []
  dirty.value = false
}
</script>

<template>
  <div>
    <div class="main-head">
      <div>
        <div class="main-title">{{ exam.title }}</div>
        <div class="main-sub">{{ exam.pages.length }} trang · gửi lúc {{ formatDateTime(exam.uploaded_at) }}</div>
      </div>
      <span class="spacer" />
      <button class="btn btn-ghost btn-icon" title="Xóa đề này" aria-label="Xóa đề" @click="deleteExam">
        <Trash2 :size="16" />
      </button>
      <StatusBadge status="pending" />
    </div>
    <div class="main-body">
      <div class="page-toolbar">
        <span class="hint"><Move :size="15" /> Kéo thả để đổi thứ tự</span>
        <span class="hint"><Trash2 :size="15" /> Di chuột vào trang để xóa</span>
      </div>
      <PageGrid :items="gridItems" draggable deletable @reorder="onReorder" @delete="onDelete" />
      <div class="submit-bar">
        <button
          class="btn btn-primary"
          title="Lưu lại thứ tự và các trang bạn vừa chỉnh sửa"
          :disabled="!dirty"
          @click="save"
        >
          Lưu thay đổi
        </button>
      </div>
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
.page-toolbar { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; flex-wrap: wrap; }
.submit-bar {
  position: sticky; bottom: 0; margin: 26px -26px -26px; padding: 14px 26px;
  background: linear-gradient(transparent, var(--bg) 30%);
  display: flex; justify-content: flex-end; gap: 10px;
}
</style>
