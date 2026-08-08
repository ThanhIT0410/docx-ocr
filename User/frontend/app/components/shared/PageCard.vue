<script setup lang="ts">
import { Trash2 } from '@lucide/vue'

defineProps<{
  pageNo: number
  source: string
  imageUrl?: string | null
  draggable?: boolean
  deletable?: boolean
}>()
const emit = defineEmits<{ delete: []; open: [] }>()
</script>

<template>
  <div class="page-card" :class="{ draggable }">
    <div class="sheet" :class="{ zoomable: imageUrl }" title="Bấm để xem ảnh lớn" @click="imageUrl && emit('open')">
      <img v-if="imageUrl" :src="imageUrl" :alt="`Trang ${pageNo}`" loading="lazy">
      <div v-else class="sheet-skeleton" aria-hidden="true" />
    </div>
    <div class="page-foot">
      <span class="page-no">Trang {{ pageNo }}</span>
      <span class="page-src" :title="source">{{ source }}</span>
    </div>
    <button
      v-if="deletable"
      class="page-del"
      :title="`Xóa riêng trang ${pageNo} này`"
      :aria-label="`Xóa trang ${pageNo}`"
      @click.stop="emit('delete')"
    >
      <Trash2 :size="14" :stroke-width="1.8" />
    </button>
  </div>
</template>

<style scoped>
.page-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  overflow: hidden; box-shadow: var(--shadow-sm); position: relative;
  transition: box-shadow .15s, opacity .15s, transform .15s;
}
.page-card.draggable { cursor: grab; }
.page-card.draggable:active { cursor: grabbing; }
.page-card:hover .page-del { opacity: 1; }
.sheet { aspect-ratio: 3/4; background: #fff; border-bottom: 1px solid var(--line); overflow: hidden; }
.sheet.zoomable { cursor: zoom-in; }
.sheet img { width: 100%; height: 100%; object-fit: contain; background: #fff; }
.sheet-skeleton { width: 100%; height: 100%; background: var(--surface-2); }
.page-foot {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 10px; background: var(--surface);
}
.page-no { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
.page-src {
  font-family: var(--font-mono); font-size: 10px; color: var(--faint); max-width: 90px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.page-del {
  position: absolute; top: 7px; right: 7px; width: 30px; height: 30px; border-radius: 7px;
  background: rgba(255, 255, 255, .92); border: 1px solid var(--line); color: var(--muted);
  display: grid; place-items: center; opacity: 0; transition: opacity .15s;
}
.page-del:hover { background: var(--danger-bg); color: var(--danger); border-color: transparent; }
@media (hover: none) {
  .page-del { opacity: 1; }
}
</style>
