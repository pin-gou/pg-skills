<template>
  <div class="app-layout">
    <TopBar @open-diff="showDiff = true" />
    <div v-if="store.loading" class="loading">
      <div>⏳ 加载中...</div>
    </div>
    <div v-else-if="store.errorMessage" class="error-screen">
      <h2>⚠ 加载失败</h2>
      <p>{{ store.errorMessage }}</p>
      <button class="btn" @click="store.reload()">↻ 重试</button>
    </div>
    <div v-else class="app-body">
      <Dashboard v-if="store.view === 'dashboard'" />
      <FormView v-else-if="store.view === 'form'" />
      <CanvasView v-else-if="store.view === 'canvas'" />
    </div>
    <StatusBar />
    <DiffModal
      :open="showDiff"
      @cancel="showDiff = false"
      @confirm="confirmSave"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import TopBar from '@/components/shared/TopBar.vue'
import StatusBar from '@/components/shared/StatusBar.vue'
import DiffModal from '@/components/shared/DiffModal.vue'
import Dashboard from '@/views/Dashboard.vue'
import FormView from '@/views/FormView.vue'
import CanvasView from '@/views/CanvasView.vue'

const store = useProjectStore()
const showDiff = ref(false)

function onKeyDown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    if (store.dirty && store.isValid) {
      showDiff.value = true
    }
  }
}

async function confirmSave() {
  const ok = await store.save()
  if (ok) showDiff.value = false
}

onMounted(() => {
  store.load()
  window.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeyDown)
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #1e1e1e;
  color: #d4d4d4;
  overflow: hidden;
}
.app-body {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}
.loading, .error-screen {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  color: #aaa;
}
.error-screen h2 { color: #ef5350; margin: 0; }
.btn {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 6px 16px; border-radius: 4px; font-size: 13px; cursor: pointer;
}
.btn:hover { background: #505050; }
</style>