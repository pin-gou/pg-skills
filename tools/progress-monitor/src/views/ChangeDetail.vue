<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePipelineStore } from '@/stores/pipelineStore'
import { usePolling } from '@/composables/usePolling'
import { STATUS_ICONS, STATUS_COLORS } from '@/types/pipeline'
import PipelineProgress from '@/components/PipelineProgress.vue'
import ManifestViewer from '@/components/ManifestViewer.vue'
import EventLog from '@/components/EventLog.vue'

const route = useRoute()
const router = useRouter()
const store = usePipelineStore()

const activeTab = ref('progress')
const changeName = computed(() => route.params.name as string)

const { enabled: pollingEnabled, toggle: togglePolling, start: startPolling } = usePolling(
  async () => { await store.refreshSnapshot() },
  5000,
)

const statusIcon = computed(() => {
  const s = store.snapshot?.status
  return STATUS_ICONS[s || 'pending'] || '○'
})

const statusColor = computed(() => {
  const s = store.snapshot?.status
  return STATUS_COLORS[s || 'pending'] || STATUS_COLORS.pending
})

const statusText = computed(() => {
  return store.snapshot?.status || 'loading...'
})

function goBack() {
  router.push('/')
}

onMounted(async () => {
  await store.loadChange(changeName.value)
  startPolling()
})

onUnmounted(() => {
  store.currentChange = null
  store.manifest = null
  store.snapshot = null
  store.tree = []
})
</script>

<template>
  <div class="page">
    <header class="page-header">
      <button class="back-btn" @click="goBack">← 返回列表</button>
      <h1 class="change-title">{{ changeName }}</h1>
      <span class="status-badge" :style="{ color: statusColor }">
        {{ statusIcon }} {{ statusText }}
      </span>
      <div class="spacer"></div>
      <button class="polling-toggle" @click="togglePolling">
        {{ pollingEnabled ? '⏸ 暂停刷新' : '▶ 恢复刷新' }}
      </button>
    </header>

    <div class="tab-bar">
      <button
        :class="['tab-btn', { active: activeTab === 'manifest' }]"
        @click="activeTab = 'manifest'"
      >📋 Manifest</button>
      <button
        :class="['tab-btn', { active: activeTab === 'progress' }]"
        @click="activeTab = 'progress'"
      >📊 Progress</button>
      <button
        :class="['tab-btn', { active: activeTab === 'events' }]"
        @click="activeTab = 'events'"
      >📜 Events</button>
    </div>

    <div class="tab-content">
      <div v-if="store.loading" class="loading">加载中...</div>
      <div v-else-if="store.error" class="error">{{ store.error }}</div>
      <template v-else>
        <ManifestViewer v-if="activeTab === 'manifest' && store.manifest" />
        <PipelineProgress v-if="activeTab === 'progress'" />
        <EventLog v-if="activeTab === 'events'" :change="changeName" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; }
.page-header {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 20px; background: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.back-btn {
  background: none; border: 1px solid var(--border-color);
  border-radius: 6px; padding: 6px 14px; cursor: pointer;
  font-size: 13px; color: var(--text-secondary);
  transition: all 0.15s;
}
.back-btn:hover { background: #f0f5ff; border-color: var(--color-running); color: var(--color-running); }
.change-title { font-size: 18px; font-weight: 600; }
.spacer { flex: 1; }
.status-badge { font-weight: 600; font-size: 14px; }
.polling-toggle {
  background: none; border: 1px solid var(--border-color);
  border-radius: 6px; padding: 6px 14px; cursor: pointer;
  font-size: 13px; color: var(--text-secondary); transition: all 0.15s;
}
.polling-toggle:hover { background: #f0f5ff; }

.tab-bar {
  display: flex; border-bottom: 1px solid var(--border-color);
  background: var(--bg-card); padding: 0 20px; flex-shrink: 0;
}
.tab-btn {
  background: none; border: none; padding: 12px 20px;
  cursor: pointer; font-size: 14px; color: var(--text-secondary);
  border-bottom: 2px solid transparent; transition: all 0.15s;
}
.tab-btn:hover { color: var(--color-running); }
.tab-btn.active { color: var(--color-running); border-bottom-color: var(--color-running); font-weight: 600; }

.tab-content { flex: 1; overflow: hidden; }
.loading, .error { padding: 40px; text-align: center; color: var(--text-muted); }
.error { color: var(--color-failed); }
</style>