import { ref, onUnmounted } from 'vue'

export function usePolling(fn: () => Promise<void>, intervalMs = 5000) {
  const enabled = ref(true)
  let timer: ReturnType<typeof setInterval> | null = null

  async function tick() {
    if (!enabled.value) return
    try {
      await fn()
    } catch {
      // silent
    }
  }

  function start() {
    stop()
    enabled.value = true
    tick()
    timer = setInterval(tick, intervalMs)
  }

  function stop() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function toggle() {
    if (enabled.value) {
      enabled.value = false
      stop()
    } else {
      start()
    }
  }

  onUnmounted(stop)

  return { enabled, start, stop, toggle, tick }
}