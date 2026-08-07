<script setup lang="ts">
import type { ExamStatus } from '~/types/models'

const props = defineProps<{ status: ExamStatus }>()

const label: Record<ExamStatus, string> = {
  pending: '● Chờ xử lý',
  processing: '● Đang xử lý',
  finished: '✓ Hoàn thành',
  failed: '✕ Lỗi'
}
</script>

<template>
  <span class="badge" :class="props.status">{{ label[props.status] }}</span>
</template>
