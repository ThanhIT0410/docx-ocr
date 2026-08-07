<script setup lang="ts">
defineProps<{ step: 1 | 2 }>()
</script>

<template>
  <div class="stepper">
    <div class="step" :class="step === 1 ? 'current' : 'done'">
      <span class="step-num">{{ step > 1 ? '✓' : '1' }}</span>Tải tệp
    </div>
    <div class="step-line" :class="{ done: step > 1 }" />
    <div class="step" :class="{ current: step === 2 }">
      <span class="step-num">2</span>Sắp xếp trang &amp; gửi
    </div>
  </div>
</template>

<style scoped>
.stepper { display: flex; align-items: center; gap: 0; margin-bottom: 24px; }
.step { display: flex; align-items: center; gap: 9px; font-size: 13.5px; font-weight: 600; color: var(--faint); }
.step-num {
  width: 27px; height: 27px; border-radius: 50%; display: grid; place-items: center;
  font-family: var(--font-mono); font-size: 12px;
  border: 1.5px solid var(--line); background: var(--surface); color: var(--faint);
}
.step.current { color: var(--ink); }
.step.current .step-num { border-color: var(--accent); background: var(--accent); color: #fff; }
.step.done { color: var(--accent-ink); }
.step.done .step-num { border-color: var(--accent); background: var(--accent-soft); color: var(--accent-ink); }
.step-line { width: 52px; height: 1.5px; background: var(--line); margin: 0 14px; }
.step-line.done { background: var(--accent); }
</style>
