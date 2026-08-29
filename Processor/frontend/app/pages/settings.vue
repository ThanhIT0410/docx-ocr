<script setup lang="ts">
import { AlertTriangle } from '@lucide/vue'
import type { PreprocessingSettings, ProcessorSettingsUpdate, WorkerTuningSettings } from '~/types/models'

// Backend defaults — Processor/backend/app/config/settings.py — used only
// to pre-fill the form when a field hasn't been customized yet (server
// returns `null` for those). Saving always sends the currently-displayed
// value, never `null` — simpler than a true tri-state "reset to default"
// control, and editing the field back to this same number IS resetting it.
const WORKER_TUNING_DEFAULTS: WorkerTuningSettings = {
  maxConcurrentPages: 4,
  maxConcurrentProcessingPages: 20,
  workerPollIntervalSeconds: 5,
  ocrMaxAttempts: 3,
  ocrBackoffBaseSeconds: 2,
  ocrTemperature: 0.8
}
const PREPROCESSING_DEFAULTS: PreprocessingSettings = {
  deskew: true,
  deskewMaxAngleDeg: 15,
  enhanceContrast: true,
  contrastClipLimit: 2.0,
  contrastTileGridSize: 8
}
const MAX_PIXELS_DEFAULT = 11289600

const isElectron = useIsElectron()
const pipeline = usePipelineStore()

const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const savedAt = ref<number | null>(null)

const supabaseUrl = ref('')
const hasServiceRoleKey = ref(false)
const hasAccessToken = ref(false)
const serviceRoleKeyInput = ref('')
const accessTokenInput = ref('')
const llamacppModel = ref('')
const maxPixels = ref<number>(MAX_PIXELS_DEFAULT)
const maxPixelsOriginal = ref<number | null>(null)
const workerTuning = reactive<WorkerTuningSettings>({ ...WORKER_TUNING_DEFAULTS })
const preprocessing = reactive<PreprocessingSettings>({ ...PREPROCESSING_DEFAULTS })

// Raw values exactly as the server returned them (nulls kept) — needed so
// save() can tell "operator actually changed this field" apart from "this
// field just happens to display the default because it was never
// customized". Without this, saving would permanently bake in today's
// backend defaults for every untouched field the very first time the page
// is saved for any reason (e.g. just updating the Supabase URL), and a
// later backend version changing its own default would never take effect
// for this install again.
const workerTuningOriginal = reactive<WorkerTuningSettings>({ ...WORKER_TUNING_DEFAULTS })
const preprocessingOriginal = reactive<PreprocessingSettings>({ ...PREPROCESSING_DEFAULTS })

/** Per-field: the form displays `original ?? default`. If `current` still
 * equals that (operator never touched it), send the original back
 * (possibly `null`) so an un-customized field stays un-customized; only a
 * genuine edit sends a concrete override. */
function resolveClusterPayload<T extends object>(original: T, current: T, defaults: T): T {
  const out = {} as T
  for (const key of Object.keys(defaults) as (keyof T)[]) {
    const displayed = original[key] ?? defaults[key]
    out[key] = current[key] === displayed ? original[key] : current[key]
  }
  return out
}

const locked = computed(() => pipeline.running)

onMounted(async () => {
  pipeline.startPolling()
  if (!isElectron) {
    loading.value = false
    return
  }
  try {
    const settings = await useElectronBridge()!.getSettings()
    supabaseUrl.value = settings.supabaseUrl
    hasServiceRoleKey.value = settings.hasServiceRoleKey
    hasAccessToken.value = settings.hasAccessToken
    llamacppModel.value = settings.llamacppModel
    maxPixelsOriginal.value = settings.maxPixels
    maxPixels.value = settings.maxPixels ?? MAX_PIXELS_DEFAULT
    Object.assign(workerTuningOriginal, settings.workerTuning)
    Object.assign(workerTuning, {
      maxConcurrentPages: settings.workerTuning.maxConcurrentPages ?? WORKER_TUNING_DEFAULTS.maxConcurrentPages,
      maxConcurrentProcessingPages: settings.workerTuning.maxConcurrentProcessingPages ?? WORKER_TUNING_DEFAULTS.maxConcurrentProcessingPages,
      workerPollIntervalSeconds: settings.workerTuning.workerPollIntervalSeconds ?? WORKER_TUNING_DEFAULTS.workerPollIntervalSeconds,
      ocrMaxAttempts: settings.workerTuning.ocrMaxAttempts ?? WORKER_TUNING_DEFAULTS.ocrMaxAttempts,
      ocrBackoffBaseSeconds: settings.workerTuning.ocrBackoffBaseSeconds ?? WORKER_TUNING_DEFAULTS.ocrBackoffBaseSeconds,
      ocrTemperature: settings.workerTuning.ocrTemperature ?? WORKER_TUNING_DEFAULTS.ocrTemperature
    })
    Object.assign(preprocessingOriginal, settings.preprocessing)
    Object.assign(preprocessing, {
      deskew: settings.preprocessing.deskew ?? PREPROCESSING_DEFAULTS.deskew,
      deskewMaxAngleDeg: settings.preprocessing.deskewMaxAngleDeg ?? PREPROCESSING_DEFAULTS.deskewMaxAngleDeg,
      enhanceContrast: settings.preprocessing.enhanceContrast ?? PREPROCESSING_DEFAULTS.enhanceContrast,
      contrastClipLimit: settings.preprocessing.contrastClipLimit ?? PREPROCESSING_DEFAULTS.contrastClipLimit,
      contrastTileGridSize: settings.preprocessing.contrastTileGridSize ?? PREPROCESSING_DEFAULTS.contrastTileGridSize
    })
  } catch (err) {
    loadError.value = 'Không tải được cài đặt hiện tại.'
    // eslint-disable-next-line no-console
    console.error('[settings] getSettings failed', err)
  } finally {
    loading.value = false
  }
})
onBeforeUnmount(() => pipeline.stopPolling())

async function save() {
  if (locked.value || saving.value) return
  saving.value = true
  savedAt.value = null
  try {
    const maxPixelsDisplayed = maxPixelsOriginal.value ?? MAX_PIXELS_DEFAULT
    const payload: ProcessorSettingsUpdate = {
      supabaseUrl: supabaseUrl.value.trim(),
      llamacppModel: llamacppModel.value.trim(),
      maxPixels: maxPixels.value === maxPixelsDisplayed ? maxPixelsOriginal.value : maxPixels.value,
      workerTuning: resolveClusterPayload(workerTuningOriginal, workerTuning, WORKER_TUNING_DEFAULTS),
      preprocessing: resolveClusterPayload(preprocessingOriginal, preprocessing, PREPROCESSING_DEFAULTS)
    }
    if (serviceRoleKeyInput.value.trim()) payload.supabaseServiceRoleKey = serviceRoleKeyInput.value.trim()
    if (accessTokenInput.value.trim()) payload.supabaseAccessToken = accessTokenInput.value.trim()

    await useElectronBridge()!.saveSettings(payload)
    if (payload.supabaseServiceRoleKey) hasServiceRoleKey.value = true
    if (payload.supabaseAccessToken) hasAccessToken.value = true
    serviceRoleKeyInput.value = ''
    accessTokenInput.value = ''
    // What was just written is now "original" — so a later save (in the
    // same session) that leaves a field untouched again correctly treats
    // it as still-untouched relative to this point, not the page-load one.
    maxPixelsOriginal.value = payload.maxPixels ?? null
    Object.assign(workerTuningOriginal, payload.workerTuning)
    Object.assign(preprocessingOriginal, payload.preprocessing)
    savedAt.value = Date.now()
    useToast().info('Đã lưu cài đặt')
  } catch (err) {
    useToast().error('Không thể lưu cài đặt')
    // eslint-disable-next-line no-console
    console.error('[settings] saveSettings failed', err)
  } finally {
    saving.value = false
  }
}

async function relaunchNow() {
  await useElectronBridge()!.relaunch()
}
</script>

<template>
  <main class="main">
    <div class="main-head">
      <div class="main-title">Cài đặt</div>
    </div>

    <div class="main-body">
      <div v-if="!isElectron" class="empty-note" style="padding-top:60px">
        Trang này chỉ khả dụng trong bản đóng gói Electron — đang chạy ở chế độ trình duyệt (nuxt dev),
        cấu hình kết nối lấy từ biến môi trường NUXT_PUBLIC_PROCESSOR_*.
      </div>

      <template v-else-if="loading">
        <div class="empty-note" style="padding-top:60px">Đang tải…</div>
      </template>

      <template v-else>
        <div v-if="loadError" class="banner banner-error">{{ loadError }}</div>

        <div v-if="locked" class="banner banner-warning">
          <AlertTriangle :size="15" />
          <span>Không thể sửa cài đặt khi OCR đang xử lý — dừng lại trước (đợi hết đề đang xử lý, hoặc chờ pipeline tự dừng nếu gặp lỗi).</span>
        </div>

        <fieldset class="form" :disabled="locked">
          <div class="section-head">Kết nối Supabase</div>
          <div class="field">
            <label for="supabaseUrl">Supabase URL</label>
            <input id="supabaseUrl" v-model="supabaseUrl" type="text" placeholder="https://xxxx.supabase.co">
          </div>
          <div class="field">
            <label for="serviceRoleKey">Service Role Key</label>
            <input
              id="serviceRoleKey" v-model="serviceRoleKeyInput" type="password" autocomplete="off"
              :placeholder="hasServiceRoleKey ? '•••••••• (đã thiết lập, để trống nếu không đổi)' : 'sb_secret_...'"
            >
          </div>
          <div class="field">
            <label for="accessToken">Access Token <span class="optional">(tuỳ chọn — số liệu dung lượng ở Dashboard)</span></label>
            <input
              id="accessToken" v-model="accessTokenInput" type="password" autocomplete="off"
              :placeholder="hasAccessToken ? '•••••••• (đã thiết lập, để trống nếu không đổi)' : 'Để trống nếu không dùng'"
            >
          </div>

          <div class="section-head">Model OCR</div>
          <div class="field">
            <label for="llamacppModel">Tên model llama.cpp</label>
            <input id="llamacppModel" v-model="llamacppModel" type="text" placeholder="(tự động theo model đã tải)">
          </div>
          <div class="field">
            <label for="maxPixels">
              Kích thước ảnh tối đa sau resize (pixel)
              <span class="optional">— giảm để OCR nhanh hơn, đánh đổi độ chi tiết nhận dạng</span>
            </label>
            <input id="maxPixels" v-model.number="maxPixels" type="number" min="65536" step="65536">
          </div>

          <details class="cluster">
            <summary>Worker tuning</summary>
            <div class="cluster-body">
              <div class="field-row">
                <label for="maxConcurrentPages">Số trang OCR song song (toàn bộ các đề đang xử lý)</label>
                <input id="maxConcurrentPages" v-model.number="workerTuning.maxConcurrentPages" type="number" min="1">
              </div>
              <div class="field-row">
                <label for="maxConcurrentProcessingPages">Số trang tối đa đang xử lý cùng lúc</label>
                <input id="maxConcurrentProcessingPages" v-model.number="workerTuning.maxConcurrentProcessingPages" type="number" min="1">
              </div>
              <div class="field-row">
                <label for="pollInterval">Chu kỳ kiểm tra hàng đợi (giây)</label>
                <input id="pollInterval" v-model.number="workerTuning.workerPollIntervalSeconds" type="number" min="1" step="0.5">
              </div>
              <div class="field-row">
                <label for="ocrMaxAttempts">Số lần thử lại khi lỗi kết nối</label>
                <input id="ocrMaxAttempts" v-model.number="workerTuning.ocrMaxAttempts" type="number" min="1">
              </div>
              <div class="field-row">
                <label for="ocrBackoff">Backoff giữa các lần thử (giây)</label>
                <input id="ocrBackoff" v-model.number="workerTuning.ocrBackoffBaseSeconds" type="number" min="0" step="0.5">
              </div>
              <div class="field-row">
                <label for="ocrTemperature">Temperature</label>
                <input id="ocrTemperature" v-model.number="workerTuning.ocrTemperature" type="number" min="0" max="2" step="0.1">
              </div>
            </div>
          </details>

          <details class="cluster">
            <summary>Tiền xử lý ảnh</summary>
            <div class="cluster-body">
              <label class="field-row checkbox-row">
                <input v-model="preprocessing.deskew" type="checkbox">
                <span>Tự động chỉnh nghiêng (deskew)</span>
              </label>
              <div class="field-row">
                <label for="deskewAngle">Góc lệch tối đa xử lý (độ)</label>
                <input id="deskewAngle" v-model.number="preprocessing.deskewMaxAngleDeg" type="number" min="0" max="45" :disabled="!preprocessing.deskew">
              </div>
              <label class="field-row checkbox-row">
                <input v-model="preprocessing.enhanceContrast" type="checkbox">
                <span>Tăng cường độ tương phản (CLAHE)</span>
              </label>
              <div class="field-row">
                <label for="clipLimit">Clip limit</label>
                <input id="clipLimit" v-model.number="preprocessing.contrastClipLimit" type="number" min="0.1" step="0.1" :disabled="!preprocessing.enhanceContrast">
              </div>
              <div class="field-row">
                <label for="tileGrid">Tile grid size</label>
                <input id="tileGrid" v-model.number="preprocessing.contrastTileGridSize" type="number" min="1" :disabled="!preprocessing.enhanceContrast">
              </div>
            </div>
          </details>

          <div class="save-row">
            <button class="btn btn-primary" :disabled="saving" @click="save">
              {{ saving ? 'Đang lưu…' : 'Lưu cài đặt' }}
            </button>
            <span v-if="savedAt" class="hint">Đã lưu — cần khởi động lại để áp dụng.</span>
            <button v-if="savedAt" class="btn btn-ghost" @click="relaunchNow">Khởi động lại ngay</button>
          </div>
        </fieldset>
      </template>
    </div>
  </main>
</template>

<style scoped>
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.main-head {
  padding: 16px 26px; border-bottom: 1px solid var(--line); background: var(--surface);
  display: flex; align-items: center; min-height: 68px;
}
.main-title { font-size: 18px; font-weight: 700; letter-spacing: -.01em; }
.main-body { flex: 1; overflow-y: auto; padding: 26px; max-width: 560px; }

.banner {
  display: flex; align-items: flex-start; gap: 9px; font-size: 13px;
  border-radius: var(--radius); padding: 12px 14px; margin-bottom: 20px;
}
.banner-warning { color: var(--pending); background: var(--pending-bg); border: 1px solid var(--pending); }
.banner-error { color: var(--danger); background: var(--danger-bg); border: 1px solid var(--danger); }
.banner svg { flex: none; margin-top: 1px; }

.form { border: none; padding: 0; margin: 0; }
.form:disabled { opacity: .55; }

.section-head {
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); margin: 24px 0 10px;
}
.section-head:first-child { margin-top: 0; }

.field { margin-bottom: 14px; }
.field label { display: block; font-size: 13px; color: var(--ink); margin-bottom: 6px; }
.field .optional { font-weight: 400; color: var(--faint); text-transform: none; letter-spacing: normal; }
.field input {
  width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px;
  font-size: 14px; background: var(--surface-2);
}
.field input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

.cluster { border: 1px solid var(--line); border-radius: var(--radius); margin-bottom: 14px; background: var(--surface); }
.cluster summary { padding: 12px 16px; font-size: 13.5px; font-weight: 600; cursor: pointer; }
.cluster-body { padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
.field-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.field-row label { font-size: 13px; color: var(--muted); }
.field-row input[type="number"] {
  width: 110px; border: 1px solid var(--line); border-radius: 7px; padding: 6px 9px;
  font-size: 13px; font-family: var(--font-mono); background: var(--surface-2); text-align: right;
}
.field-row input[type="number"]:disabled { opacity: .5; }
.checkbox-row { justify-content: flex-start; gap: 9px; }
.checkbox-row input[type="checkbox"] { accent-color: var(--accent); width: 15px; height: 15px; }
.checkbox-row span { color: var(--ink); }

.save-row { display: flex; align-items: center; gap: 12px; margin-top: 22px; }
</style>
