<script setup lang="ts">
const store = useDocumentsStore()

onMounted(() => {
  const first = store.pending[0] ?? store.processing[0] ?? store.finished[0]
  if (first) navigateTo(`/documents/${first.id}`)
})
</script>

<template>
  <main class="main">
    <div class="main-head"><div class="main-title">Tất cả tài liệu</div></div>
    <div class="main-body">
      <div class="empty-note" style="padding-top:60px">
        {{ store.pending.length || store.processing.length || store.finished.length
          ? 'Chọn một tài liệu ở thanh bên để xem.'
          : 'Chưa có tài liệu nào — hãy tải đề lên để bắt đầu.' }}
      </div>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head { padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface); min-height: 68px; display: flex; align-items: center; }
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-body { flex: 1; overflow-y: auto; padding: 26px; }
</style>
