<script setup lang="ts">
const route = useRoute()
const store = useDocumentsStore()

const id = computed(() => String(route.params.id))

watch(id, (value) => store.openExam(value), { immediate: true })
</script>

<template>
  <main class="main">
    <template v-if="store.activeExam && store.activeExam.id === id">
      <PendingPanel v-if="store.activeExam.status === 'pending'" :exam="store.activeExam" />
      <ProcessingPanel v-else-if="store.activeExam.status === 'processing'" :exam="store.activeExam" />
      <FinishedPanel v-else :exam="store.activeExam" />
    </template>
    <div v-else-if="store.loadingActive" class="main-body">
      <div class="empty-note" style="padding-top:60px">Đang tải…</div>
    </div>
    <div v-else class="main-body">
      <div class="empty-note" style="padding-top:60px">Không tìm thấy tài liệu này.</div>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-body { flex: 1; overflow-y: auto; padding: 26px; }
</style>
