interface ToastState {
  message: string
  variant: 'info' | 'error'
  visible: boolean
}

const state = reactive<ToastState>({ message: '', variant: 'info', visible: false })
let hideTimer: ReturnType<typeof setTimeout> | undefined

/** App-wide toast, same pattern as User/frontend's useToast. */
export function useToast() {
  function show(message: string, variant: ToastState['variant'] = 'info') {
    state.message = message
    state.variant = variant
    state.visible = true
    clearTimeout(hideTimer)
    hideTimer = setTimeout(() => { state.visible = false }, 2200)
  }
  return {
    state: readonly(state),
    info: (msg: string) => show(msg, 'info'),
    error: (msg: string) => show(msg, 'error')
  }
}
