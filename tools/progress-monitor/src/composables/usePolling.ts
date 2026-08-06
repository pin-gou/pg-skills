import { getCurrentScope, onScopeDispose, ref } from 'vue'

export function usePolling(fn: () => Promise<void>, intervalMs = 5000) {
  const enabled = ref(false)
  const pending = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  function schedule() {
    clearTimer()
    if (!enabled.value) return
    timer = setTimeout(() => {
      timer = null
      void tick()
    }, intervalMs)
  }

  async function tick() {
    if (!enabled.value || pending.value) return
    pending.value = true
    try {
      await fn()
    } catch {
      // silent
    } finally {
      pending.value = false
      schedule()
    }
  }

  function start() {
    clearTimer()
    enabled.value = true
    if (!pending.value) void tick()
  }

  function stop() {
    enabled.value = false
    clearTimer()
  }

  function toggle() {
    if (enabled.value) {
      enabled.value = false
      stop()
    } else {
      start()
    }
  }

  if (getCurrentScope()) onScopeDispose(stop)

  return { enabled, pending, start, stop, toggle, tick }
}
