<script setup lang="ts">
import draggable from 'vuedraggable'
import type { UploadItem } from '~/types/models'
import { GripVertical, X } from '@lucide/vue'

const props = defineProps<{ items: UploadItem[] }>()
const emit = defineEmits<{ reorder: [orderedIds: string[]]; remove: [id: string] }>()

const model = computed({
  get: () => props.items,
  set: (val: UploadItem[]) => emit('reorder', val.map(v => v.id))
})

async function confirmRemove(item: UploadItem) {
  const ok = await useConfirm().confirm({
    title: 'Xóa tệp này?',
    message: `"${item.fileName}" sẽ bị bỏ khỏi danh sách. Bạn sẽ cần tải lại nếu muốn thêm lại.`,
    danger: true,
    confirmLabel: 'Xóa'
  })
  if (ok) emit('remove', item.id)
}
</script>

<template>
  <div class="queue">
    <div class="queue-label">Đề chờ tải lên · kéo để sắp thứ tự</div>
    <draggable v-model="model" item-key="id" handle=".grip" class="queue-list" ghost-class="drop-target" drag-class="dragging">
      <template #item="{ element: f }">
        <div class="file-row">
          <span class="grip" title="Giữ và kéo để đổi thứ tự đề">
            <GripVertical :size="16" />
          </span>
          <span class="file-kind" :class="f.kind" :title="f.kind === 'pdf' ? 'Tệp PDF' : 'Bộ ảnh'">
            {{ f.kind === 'pdf' ? 'PDF' : 'ẢNH' }}
          </span>
          <span class="file-name" :title="f.fileName">{{ f.fileName }}</span>
          <span v-if="f.error" class="file-status error">{{ f.error }}</span>
          <template v-else-if="f.splitting">
            <span class="split-bar" title="Đang xử lý tệp"><i /></span>
            <span class="file-status splitting">{{ f.kind === 'pdf' ? 'đang tách trang…' : 'đang xử lý…' }}</span>
          </template>
          <span v-else class="file-status" title="Số trang trong đề này">{{ f.pages.length }} trang</span>
          <button class="file-remove" :title="`Xóa ${f.fileName}`" :aria-label="`Xóa ${f.fileName}`" @click="confirmRemove(f)">
            <X :size="16" />
          </button>
        </div>
      </template>
    </draggable>
  </div>
</template>

<style scoped>
.queue { margin-top: 22px; display: flex; flex-direction: column; gap: 8px; }
.queue-label { font-size: 12.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 2px; }
.queue-list { display: flex; flex-direction: column; gap: 8px; }
.file-row {
  display: flex; align-items: center; gap: 12px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  box-shadow: var(--shadow-sm);
}
:deep(.dragging) { opacity: .45; }
:deep(.drop-target) { box-shadow: 0 0 0 2px var(--accent); }
.grip { color: #B9C4D8; cursor: grab; flex: none; display: grid; place-items: center; width: 22px; }
.grip:active { cursor: grabbing; }
.file-kind {
  flex: none; font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
  border-radius: 5px; padding: 3px 8px; letter-spacing: .03em;
}
.file-kind.pdf { background: var(--danger-bg); color: var(--danger); }
.file-kind.img { background: var(--finished-bg); color: var(--finished); }
.file-name {
  flex: 1; min-width: 0; font-size: 14px; font-weight: 600; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.file-status { font-family: var(--font-mono); font-size: 12px; color: var(--muted); flex: none; }
.file-status.splitting { color: var(--accent); }
.file-status.error { color: var(--danger); }
.file-remove { color: var(--faint); flex: none; width: 32px; height: 32px; border-radius: 7px; display: grid; place-items: center; }
.file-remove:hover { background: var(--danger-bg); color: var(--danger); }
.split-bar { height: 4px; border-radius: 3px; background: var(--line); overflow: hidden; width: 90px; flex: none; }
.split-bar i { display: block; height: 100%; width: 40%; background: var(--accent); border-radius: 3px; animation: slide 1.1s ease-in-out infinite; }
@keyframes slide { 0% { transform: translateX(-100%); } 100% { transform: translateX(250%); } }
</style>
