<script setup lang="ts">
const documentsStore = useDocumentsStore()

onMounted(() => {
  documentsStore.fetchLists()
  documentsStore.subscribeRealtime()
})

onBeforeUnmount(() => {
  documentsStore.unsubscribeRealtime()
})
</script>

<template>
  <div class="app-shell">
    <PrimarySidebar />
    <SecondarySidebar />
    <NuxtPage />
  </div>
  <AppToast />
</template>
