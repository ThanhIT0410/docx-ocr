<script setup lang="ts">
import { ChevronLeft, ChevronRight, X } from '@lucide/vue'
import type { PageGridItem } from '~/components/shared/PageGrid.vue'

const props = defineProps<{
  items: PageGridItem[]
  index: number
}>()

const emit = defineEmits<{
  'update:index': [index: number]
  close: []
}>()

const current = computed(() => props.items[props.index])

function go(delta: number) {
  const next = props.index + delta
  if (next < 0 || next >= props.items.length) return
  emit('update:index', next)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
  else if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'ArrowRight') go(1)
}

// Only mounted while the lightbox is open (parent uses v-if), so a plain
// mount/unmount pair is enough — no need to guard on an `open` prop.
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="lightbox-backdrop" @click.self="emit('close')">
    <button class="lightbox-close" title="Đóng (Esc)" aria-label="Đóng" @click="emit('close')">
      <X :size="22" />
    </button>

    <button
      v-if="items.length > 1"
      class="lightbox-nav lightbox-prev"
      title="Trang trước"
      aria-label="Trang trước"
      :disabled="index === 0"
      @click="go(-1)"
    >
      <ChevronLeft :size="26" />
    </button>

    <div class="lightbox-body">
      <img v-if="current?.imageUrl" :src="current.imageUrl" :alt="`Trang ${index + 1}`">
      <div class="lightbox-caption">
        Trang {{ index + 1 }} / {{ items.length }}
        <span v-if="current?.source" class="lightbox-source"> · {{ current.source }}</span>
      </div>
    </div>

    <button
      v-if="items.length > 1"
      class="lightbox-nav lightbox-next"
      title="Trang sau"
      aria-label="Trang sau"
      :disabled="index === items.length - 1"
      @click="go(1)"
    >
      <ChevronRight :size="26" />
    </button>
  </div>
</template>

<style scoped>
.lightbox-backdrop {
  position: fixed; inset: 0; z-index: 1000; background: rgba(10, 14, 24, .88);
  display: flex; align-items: center; justify-content: center; padding: 40px;
}
.lightbox-body { display: flex; flex-direction: column; align-items: center; gap: 12px; max-width: 100%; max-height: 100%; }
.lightbox-body img {
  max-width: calc(100vw - 160px); max-height: calc(100vh - 120px);
  object-fit: contain; background: #fff; border-radius: 6px; box-shadow: 0 12px 40px rgba(0, 0, 0, .4);
}
.lightbox-caption { font-family: var(--font-mono); font-size: 12.5px; color: #fff; opacity: .85; }
.lightbox-source { opacity: .7; }
.lightbox-close {
  position: absolute; top: 20px; right: 20px; width: 40px; height: 40px; border-radius: 8px;
  background: rgba(255, 255, 255, .1); border: 1px solid rgba(255, 255, 255, .2); color: #fff;
  display: grid; place-items: center; cursor: pointer; transition: background .15s;
}
.lightbox-close:hover { background: rgba(255, 255, 255, .2); }
.lightbox-nav {
  position: absolute; top: 50%; transform: translateY(-50%); width: 48px; height: 48px; border-radius: 50%;
  background: rgba(255, 255, 255, .1); border: 1px solid rgba(255, 255, 255, .2); color: #fff;
  display: grid; place-items: center; cursor: pointer; transition: background .15s;
}
.lightbox-nav:hover:not(:disabled) { background: rgba(255, 255, 255, .2); }
.lightbox-nav:disabled { opacity: .25; cursor: default; }
.lightbox-prev { left: 20px; }
.lightbox-next { right: 20px; }
</style>
