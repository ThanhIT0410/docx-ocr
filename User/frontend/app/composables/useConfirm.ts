interface ConfirmState {
  visible: boolean
  title: string
  message: string
  /** 'confirm' shows Hủy/Xác nhận (used before a destructive, reversible-
   * only-by-redoing action). 'alert' shows a single acknowledge button —
   * for explaining why an action can't proceed (e.g. deleting an exam
   * that's currently processing), not for asking permission. */
  mode: 'confirm' | 'alert'
  danger: boolean
  confirmLabel: string
}

const state = reactive<ConfirmState>({
  visible: false, title: '', message: '', mode: 'confirm', danger: false, confirmLabel: 'Xác nhận'
})
let resolver: ((value: boolean) => void) | null = null

interface DialogOptions {
  title: string
  message: string
  /** Only meaningful for `confirm()` — 'alert' always uses a neutral primary button. */
  danger?: boolean
  confirmLabel?: string
}

/** App-wide confirm/alert dialog — a single shared instance (mirrors
 * useToast.ts's pattern), replacing raw `window.confirm()` so every
 * "are you sure?" / "you can't do that" moment in the app looks and
 * behaves the same way. */
export function useConfirm() {
  function open(opts: DialogOptions, mode: ConfirmState['mode']): Promise<boolean> {
    state.title = opts.title
    state.message = opts.message
    state.mode = mode
    state.danger = opts.danger ?? false
    state.confirmLabel = opts.confirmLabel ?? (mode === 'alert' ? 'Đã hiểu' : 'Xác nhận')
    state.visible = true
    return new Promise<boolean>((resolve) => { resolver = resolve })
  }

  function respond(value: boolean) {
    state.visible = false
    resolver?.(value)
    resolver = null
  }

  return {
    state: readonly(state),
    /** Resolves `true` if the user confirmed, `false` if cancelled/dismissed. */
    confirm: (opts: DialogOptions) => open(opts, 'confirm'),
    /** Resolves once acknowledged — nothing to branch on, just await it if
     * the caller wants to wait for the dialog to close. */
    alert: (opts: DialogOptions) => open(opts, 'alert'),
    respond
  }
}
