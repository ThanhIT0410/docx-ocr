<script setup lang="ts">
import { Info, Move, Trash2 } from '@lucide/vue'
import type { PageGridItem } from '~/components/shared/PageGrid.vue'

const store = useUploadStore()

function onDrop(files: FileList) {
  store.addFiles(files)
}

const gridItems = computed<PageGridItem[]>(() =>
  store.pages.map(p => ({ id: p.id, source: p.source, imageUrl: p.filePath }))
)
</script>

<template>
  <main class="main">
    <div class="main-head">
      <div>
        <div class="main-title">Tải đề lên</div>
        <div class="main-sub">Ảnh chụp hoặc tệp PDF — PDF sẽ được tách thành từng trang</div>
      </div>
    </div>

    <div class="main-body">
      <UploadStepper :step="store.step" />

      <template v-if="store.step === 1">
        <Dropzone @files="onDrop" />
        <FileQueue
          v-if="store.items.length"
          :items="store.items"
          @reorder="store.setItemsOrder"
          @remove="store.removeItem"
          @rename="store.renameItem"
        />
        <div v-if="store.items.length" class="flow-actions">
          <button
            class="btn btn-primary"
            title="Xem lại và sắp xếp thứ tự các trang trước khi gửi"
            :disabled="!store.readyForStep2"
            @click="store.goToStep2()"
          >
            Tiếp tục — sắp xếp trang
          </button>
        </div>
      </template>

      <template v-else>
        <div class="hint current-title">
          <Info :size="16" />
          Đang sắp xếp: {{ store.combinedTitle || 'Đề của bạn' }}
        </div>
        <div class="page-toolbar">
          <span class="hint" title="Nhấn giữ vào một trang rồi kéo sang vị trí khác"><Move :size="15" /> Kéo thả để đổi thứ tự trang</span>
          <span class="hint" title="Đưa chuột vào trang, nút xóa sẽ hiện ở góc trên bên phải"><Trash2 :size="15" /> Di chuột vào trang để xóa</span>
          <span class="spacer" />
          <span class="hint mono">{{ store.pages.length }} trang</span>
        </div>
        <PageGrid
          :items="gridItems"
          draggable
          deletable
          @reorder="store.setPagesOrder"
          @delete="store.removePage"
        />
        <div class="submit-bar">
          <button class="btn btn-ghost" title="Quay lại bước tải tệp, có thể thêm hoặc bớt tệp" @click="store.backToStep1()">
            ← Quay lại
          </button>
          <button
            class="btn btn-primary"
            title="Gửi đề để hệ thống bắt đầu xử lý"
            :disabled="!store.pages.length || store.submitting"
            @click="store.submit()"
          >
            {{ store.submitting ? 'Đang gửi…' : 'Gửi đề' }}
          </button>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 14px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-sub { font-size: 13px; color: var(--muted); }
.main-body { flex: 1; overflow-y: auto; padding: 26px; position: relative; }
.spacer { flex: 1; }
.current-title { margin-bottom: 14px; font-size: 14.5px; color: var(--ink); font-weight: 600; }
.page-toolbar { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; flex-wrap: wrap; }
.mono { font-family: var(--font-mono); }
.flow-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; }
.submit-bar {
  position: sticky; bottom: 0; margin: 26px -26px -26px; padding: 14px 26px;
  background: linear-gradient(transparent, var(--bg) 30%);
  display: flex; justify-content: flex-end; gap: 10px;
}
</style>
