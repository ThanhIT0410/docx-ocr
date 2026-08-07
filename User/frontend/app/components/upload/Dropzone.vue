<script setup lang="ts">
import { UploadCloud } from '@lucide/vue'

const emit = defineEmits<{ files: [FileList] }>()
const dragOver = ref(false)
const input = ref<HTMLInputElement>()

function openPicker() { input.value?.click() }
function onDrop(e: DragEvent) {
  dragOver.value = false
  if (e.dataTransfer?.files?.length) emit('files', e.dataTransfer.files)
}
function onPick(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files?.length) emit('files', files)
  ;(e.target as HTMLInputElement).value = ''
}
</script>

<template>
  <div
    class="dropzone"
    :class="{ dragover: dragOver }"
    role="button"
    tabindex="0"
    title="Bấm vào đây để chọn tệp từ máy tính, hoặc kéo tệp rồi thả vào vùng này"
    aria-label="Chọn hoặc kéo thả tệp"
    @click="openPicker"
    @keydown.enter="openPicker"
    @keydown.space.prevent="openPicker"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
  >
    <UploadCloud class="dz-icon" :size="46" :stroke-width="1.5" />
    <div class="dz-title">Kéo thả tệp vào đây, hoặc bấm để chọn</div>
    <div class="dz-sub">Mỗi đề là một bộ ảnh hoặc một tệp PDF</div>
    <div class="dz-formats">JPG · PNG · PDF — tối đa 50MB/tệp</div>
    <input ref="input" type="file" multiple accept=".pdf,image/jpeg,image/png" hidden @change="onPick" @click.stop>
  </div>
</template>

<style scoped>
.dropzone {
  border: 1.5px dashed #B9C4D8; border-radius: 14px; background: var(--surface);
  padding: 46px 20px; text-align: center; transition: border-color .15s, background .15s;
  cursor: pointer;
}
.dropzone:hover, .dropzone.dragover { border-color: var(--accent); background: var(--accent-soft); }
.dz-icon { margin: 0 auto 12px; color: var(--accent); }
.dz-title { font-size: 16px; font-weight: 600; }
.dz-sub { font-size: 13px; color: var(--muted); margin-top: 4px; }
.dz-formats { font-family: var(--font-mono); font-size: 11.5px; color: var(--faint); margin-top: 10px; }
</style>
