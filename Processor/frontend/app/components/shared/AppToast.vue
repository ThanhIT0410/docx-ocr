<script setup lang="ts">
const { state } = useToast()
</script>

<template>
  <div class="toast" :class="{ show: state.visible, error: state.variant === 'error' }" role="status" aria-live="polite">
    {{ state.message }}
  </div>
</template>
