<script setup lang="ts">
const route = useRoute()
const showSecondary = computed(() => route.path.startsWith('/exams'))
</script>

<template>
  <div class="app-shell">
    <PrimarySidebar />
    <SecondarySidebar v-if="showSecondary" />
    <NuxtPage />
  </div>
  <AppToast />
</template>
