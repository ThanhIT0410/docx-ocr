<script setup lang="ts">
const props = defineProps<{
  label: string
  /** null = stat unavailable (access token not configured / Management API call failed). */
  bytes: number | null
  thresholdBytes: number
}>()

const percent = computed(() => {
  if (props.bytes === null) return 0
  return (props.bytes / props.thresholdBytes) * 100
})

/** Exactly 2 states, not a 3-tier gradient — matches the "an toàn hoặc báo
 * động" framing: below threshold is safe, at-or-over is alert. Going over
 * is not blocked anywhere, just visually flagged. */
const isAlert = computed(() => props.bytes !== null && props.bytes >= props.thresholdBytes)
</script>

<template>
  <div class="usage-bar">
    <div class="usage-head">
      <span class="usage-label">{{ label }}</span>
      <span v-if="bytes === null" class="usage-value muted">chưa cấu hình</span>
      <span v-else class="usage-value" :class="{ alert: isAlert }">
        {{ formatBytes(bytes) }} / {{ formatBytes(thresholdBytes) }} ({{ Math.round(percent) }}%)
      </span>
    </div>
    <div class="progress-track">
      <div
        v-if="bytes !== null"
        class="progress-fill"
        :class="isAlert ? 'alert' : 'safe'"
        :style="{ width: Math.min(percent, 100) + '%' }"
      />
    </div>
  </div>
</template>

<style scoped>
.usage-bar { display: flex; flex-direction: column; gap: 6px; }
.usage-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.usage-label { font-size: 13.5px; font-weight: 600; }
.usage-value { font-family: var(--font-mono); font-size: 12px; color: var(--muted); }
.usage-value.alert { color: var(--danger); font-weight: 600; }
.usage-value.muted { color: var(--faint); font-style: italic; }
.progress-fill.safe { background: var(--finished); }
.progress-fill.alert { background: var(--danger); }
</style>
