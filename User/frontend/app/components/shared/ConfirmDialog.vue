<script setup lang="ts">
const { state, respond } = useConfirm()

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') respond(false)
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="state.visible" class="confirm-backdrop" @click.self="respond(false)">
    <div class="confirm-box" role="alertdialog" aria-modal="true" :aria-label="state.title">
      <div class="confirm-title">{{ state.title }}</div>
      <div class="confirm-message">{{ state.message }}</div>
      <div class="confirm-actions">
        <button v-if="state.mode === 'confirm'" class="btn btn-ghost" @click="respond(false)">Hủy</button>
        <button
          class="btn"
          :class="state.danger ? 'btn-danger-solid' : 'btn-primary'"
          @click="respond(true)"
        >
          {{ state.confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.confirm-backdrop {
  position: fixed; inset: 0; z-index: 900; background: rgba(22, 33, 58, .45);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.confirm-box {
  background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow-md);
  padding: 22px 24px; max-width: 380px; width: 100%;
}
.confirm-title { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
.confirm-message { font-size: 14px; color: var(--muted); line-height: 1.55; }
.confirm-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.btn-danger-solid { background: var(--danger); color: #fff; }
.btn-danger-solid:hover { background: #A82E27; }
</style>
