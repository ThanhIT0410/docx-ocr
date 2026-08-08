<script setup lang="ts">
import draggable from 'vuedraggable'

export interface PageGridItem {
  id: string
  source: string
  imageUrl?: string | null
}

const props = defineProps<{
  items: PageGridItem[]
  draggable?: boolean
  deletable?: boolean
}>()

const emit = defineEmits<{
  /** Fires once, after a drag settles, with the full new id order. The
   * parent (a Pinia action or a local draft ref) is the source of truth —
   * this component never mutates `items` itself. */
  reorder: [orderedIds: string[]]
  delete: [id: string]
}>()

const model = computed({
  get: () => props.items,
  set: (val: PageGridItem[]) => emit('reorder', val.map(v => v.id))
})

/** Index into `items` currently shown full-size, or `null` when closed —
 * lives here (not per-`PageCard`) so Prev/Next in the lightbox can walk
 * the whole grid regardless of which card was clicked. */
const lightboxIndex = ref<number | null>(null)
</script>

<template>
  <draggable
    v-if="draggable"
    v-model="model"
    item-key="id"
    class="page-grid"
    ghost-class="drop-target"
    drag-class="dragging"
  >
    <template #item="{ element, index }">
      <PageCard
        :page-no="index + 1"
        :source="element.source"
        :image-url="element.imageUrl"
        draggable
        :deletable="deletable"
        @delete="emit('delete', element.id)"
        @open="lightboxIndex = index"
      />
    </template>
  </draggable>

  <div v-else class="page-grid">
    <PageCard
      v-for="(element, index) in items"
      :key="element.id"
      :page-no="index + 1"
      :source="element.source"
      :image-url="element.imageUrl"
      :deletable="deletable"
      @delete="emit('delete', element.id)"
      @open="lightboxIndex = index"
    />
  </div>

  <PageLightbox
    v-if="lightboxIndex !== null"
    :items="items"
    :index="lightboxIndex"
    @update:index="lightboxIndex = $event"
    @close="lightboxIndex = null"
  />
</template>

<style scoped>
.page-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px;
}
:deep(.dragging) { opacity: .4; }
:deep(.drop-target) { box-shadow: 0 0 0 2px var(--accent); }
</style>
