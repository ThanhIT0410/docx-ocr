<script setup lang="ts">
const store = useDashboardStore()
const pipeline = usePipelineStore()

onMounted(() => {
  store.startPolling()
  pipeline.startPolling()
})
onBeforeUnmount(() => {
  store.stopPolling()
  pipeline.stopPolling()
})

// Hardcoded per the user's own framing ("phía frontend hiển thị thêm mốc
// 500mb cho db và 1gb cho storage") — these are display/coloring
// thresholds, not backend config; the backend only returns raw bytes.
const DB_THRESHOLD_BYTES = 500 * 1024 ** 2
const STORAGE_THRESHOLD_BYTES = 1024 ** 3

const CONFIRM_PHRASE = 'XÓA HẾT'
const confirmInput = ref('')
const canReset = computed(() => confirmInput.value.trim() === CONFIRM_PHRASE)

async function doReset() {
  if (!canReset.value || store.resetting) return
  await store.resetAll()
  confirmInput.value = ''
}

const statusCards: Array<{ key: keyof typeof store.counts, label: string }> = [
  { key: 'pending', label: 'Chờ xử lý' },
  { key: 'processing', label: 'Đang xử lý' },
  { key: 'finished', label: 'Hoàn thành' },
  { key: 'failed', label: 'Lỗi' }
]
</script>

<template>
  <main class="main">
    <div class="main-head">
      <div class="main-title">Dashboard</div>
      <span class="spacer" />
      <span class="health" :class="{ ok: pipeline.running, bad: !!pipeline.error }">
        <span class="dot" :class="pipeline.running ? 'processing' : (pipeline.error ? 'failed' : '')" />
        OCR: {{ pipeline.running ? 'đang chạy' : (pipeline.error ? 'đã dừng' : 'chưa bật') }}
      </span>
      <span class="health" :class="{ ok: store.llamacppHealthy, bad: !store.llamacppHealthy }">
        <span class="dot" :class="store.llamacppHealthy ? 'finished' : 'failed'" />
        llama.cpp: {{ store.llamacppHealthy ? 'đang chạy' : 'mất kết nối' }}
      </span>
      <span class="health" :class="{ ok: store.apiHealthy === true, bad: store.apiHealthy === false }">
        <span class="dot" :class="store.apiHealthy ? 'finished' : 'failed'" />
        {{ store.apiHealthy === null ? 'Đang kiểm tra API…' : store.apiHealthy ? 'API đang chạy' : 'Mất kết nối API' }}
      </span>
    </div>

    <div class="main-body">
      <div v-if="store.offline" class="empty-note">Mất kết nối tới Processor API.</div>

      <template v-else>
        <div class="section-head">Tổng quan đề</div>
        <div class="stat-row">
          <div v-for="c in statusCards" :key="c.key" class="stat-card">
            <div class="stat-value">{{ store.counts[c.key] }}</div>
            <div class="stat-label">{{ c.label }}</div>
            <div v-if="c.key === 'processing'" class="stat-sub">
              {{ store.processingPages }}/{{ store.processingPagesLimit }} trang
            </div>
          </div>
        </div>

        <div class="section-head">Hôm nay</div>
        <div class="stat-row">
          <div class="stat-card">
            <div class="stat-value">{{ store.finishedToday }}</div>
            <div class="stat-label">Hoàn thành hôm nay</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ store.failedToday }}</div>
            <div class="stat-label">Lỗi hôm nay</div>
          </div>
        </div>

        <div class="section-head">Dung lượng</div>
        <div class="usage-row">
          <UsageBar label="Database" :bytes="store.dbSizeBytes" :threshold-bytes="DB_THRESHOLD_BYTES" />
          <UsageBar label="Storage" :bytes="store.storageSizeBytes" :threshold-bytes="STORAGE_THRESHOLD_BYTES" />
        </div>

        <div class="section-head">Vùng nguy hiểm</div>
        <div class="danger-card">
          <div class="danger-title">Xóa toàn bộ dữ liệu</div>
          <p class="danger-desc">
            Xóa <strong>toàn bộ</strong> bản ghi đề/trang trong Postgres và các file ảnh tương ứng trên
            Supabase Storage. Thao tác <strong>không thể hoàn tác</strong>. Bị chặn ngoài môi trường
            dev/staging (<code>PROCESSOR_ENV</code>) — nếu bấm mà không có tác dụng, đó là do
            backend đang chạy ở production.
          </p>

          <label class="confirm-label" for="confirm-input">
            Gõ chính xác <code>{{ CONFIRM_PHRASE }}</code> để bật nút xóa
          </label>
          <input
            id="confirm-input"
            v-model="confirmInput"
            class="confirm-input"
            type="text"
            autocomplete="off"
            :placeholder="CONFIRM_PHRASE"
          >

          <button class="btn btn-danger btn-outline" :disabled="!canReset || store.resetting" @click="doReset">
            {{ store.resetting ? 'Đang xóa…' : 'Xóa toàn bộ dữ liệu' }}
          </button>

          <div v-if="store.lastResetResult" class="result-box">
            <div>Đã xóa {{ store.lastResetResult.exams_deleted }} đề, {{ store.lastResetResult.pages_deleted }} trang.</div>
            <div>Storage: {{ store.lastResetResult.storage_objects_deleted }} object đã xóa.</div>
            <div v-if="store.lastResetResult.storage_objects_failed.length" class="result-fail">
              {{ store.lastResetResult.storage_objects_failed.length }} object xóa thất bại — kiểm tra log backend.
            </div>
          </div>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; gap: 18px; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.spacer { flex: 1; }
.health { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--muted); }
.main-body { flex: 1; overflow-y: auto; padding: 26px; }

.section-head {
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); margin: 26px 0 10px;
}
.section-head:first-child { margin-top: 0; }

.stat-row { display: flex; gap: 14px; margin-bottom: 12px; }
.stat-row .stat-card { flex: 1; }
.stat-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }

.usage-row {
  display: flex; gap: 24px; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow-sm);
}
.usage-row > * { flex: 1; }

.danger-card {
  max-width: 520px; background: var(--surface); border: 1px solid var(--danger);
  border-radius: var(--radius); padding: 20px 22px; box-shadow: var(--shadow-sm);
}
.danger-title { font-size: 15px; font-weight: 700; color: var(--danger); margin-bottom: 8px; }
.danger-desc { font-size: 13.5px; color: var(--muted); line-height: 1.6; margin-bottom: 18px; }
.danger-desc code { font-family: var(--font-mono); background: var(--surface-2); padding: 1px 5px; border-radius: 4px; }

.confirm-label { display: block; font-size: 13px; color: var(--ink); margin-bottom: 6px; }
.confirm-label code { font-family: var(--font-mono); font-weight: 700; }
.confirm-input {
  width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px;
  font-size: 14px; margin-bottom: 14px; background: var(--surface-2);
}
.confirm-input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

.btn-outline { border: 1px solid var(--danger); }

.result-box {
  margin-top: 16px; padding: 12px 14px; background: var(--surface-2);
  border-radius: 8px; font-size: 13px; color: var(--muted); line-height: 1.6;
}
.result-fail { color: var(--danger); font-weight: 600; }
</style>
