<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePipelineStore } from '@/stores/pipelineStore'
import { usePolling } from '@/composables/usePolling'
import { STATUS_COLORS, STATUS_ICONS } from '@/types/pipeline'
import PipelineProgress from '@/components/PipelineProgress.vue'
import ManifestViewer from '@/components/ManifestViewer.vue'
import EventLog from '@/components/EventLog.vue'

const route = useRoute()
const router = useRouter()
const store = usePipelineStore()
const activeTab = ref<'manifest' | 'progress' | 'events'>('progress')
const changeName = computed(() => route.params.name as string)

const { enabled: pollingEnabled, toggle: togglePolling, start: startPolling } = usePolling(
  () => store.refreshCurrent(),
  3000,
)

const status = computed(() => store.snapshot?.status || 'pending')
const statusIcon = computed(() => STATUS_ICONS[status.value] || '○')
const statusColor = computed(() => STATUS_COLORS[status.value] || STATUS_COLORS.pending)
const currentLocation = computed(() => {
  const snapshot = store.snapshot
  if (!snapshot) return '尚未开始'
  return [snapshot.current_stage, snapshot.current_track, snapshot.current_phase].filter(Boolean).join(' / ') || '等待下一阶段'
})
const progressText = computed(() => {
  const { completed, total } = store.progress
  return total > 0 ? `${completed}/${total} phases` : 'no phase data'
})
const refreshedText = computed(() => store.lastUpdatedAt?.toLocaleTimeString('zh-CN') || '-')

onMounted(async () => {
  await store.loadChange(changeName.value)
  startPolling()
})

onUnmounted(() => store.clearCurrent())
</script>

<template>
  <div class="page">
    <header class="page-header">
      <button class="outline-btn" @click="router.push('/')">← 返回列表</button>
      <h1>{{ changeName }}</h1>
      <span class="status-badge" :style="{ color: statusColor }">{{ statusIcon }} {{ status }}</span>
      <div class="spacer"></div>
      <span class="refresh-time">更新于 {{ refreshedText }}</span>
      <button class="outline-btn" @click="togglePolling">
        {{ pollingEnabled ? '暂停自动刷新' : '恢复自动刷新' }}
      </button>
    </header>

    <div class="summary-bar">
      <span><strong>当前位置：</strong>{{ currentLocation }}</span>
      <span><strong>进度：</strong>{{ progressText }}</span>
      <span v-if="store.snapshot?.failed_reason" class="failure"><strong>失败原因：</strong>{{ store.snapshot.failed_reason }}</span>
    </div>
    <div v-if="store.refreshError" class="refresh-error">{{ store.refreshError }}</div>

    <div class="tab-bar">
      <button :class="['tab-btn', { active: activeTab === 'manifest' }]" @click="activeTab = 'manifest'">Manifest</button>
      <button :class="['tab-btn', { active: activeTab === 'progress' }]" @click="activeTab = 'progress'">Progress</button>
      <button :class="['tab-btn', { active: activeTab === 'events' }]" @click="activeTab = 'events'">Events</button>
    </div>

    <div class="tab-content">
      <div v-if="store.loading" class="notice">加载中...</div>
      <div v-else-if="store.error" class="notice failure">{{ store.error }}</div>
      <template v-else>
        <ManifestViewer v-if="activeTab === 'manifest' && store.manifest" />
        <PipelineProgress v-if="activeTab === 'progress'" />
        <EventLog v-if="activeTab === 'events'" :change="changeName" :refresh-key="store.refreshVersion" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; }
.page-header { display: flex; align-items: center; gap: 12px; padding: 12px 20px; background: var(--bg-card); border-bottom: 1px solid var(--border-color); }
.page-header h1 { font-size: 18px; }
.spacer { flex: 1; }
.status-badge { font-weight: 600; }
.refresh-time { color: var(--text-muted); font-size: 12px; }
.outline-btn { background: none; border: 1px solid var(--border-color); border-radius: 6px; padding: 6px 12px; cursor: pointer; color: var(--text-secondary); }
.outline-btn:hover { border-color: var(--color-running); color: var(--color-running); }
.summary-bar { display: flex; gap: 24px; padding: 8px 20px; background: #f8fafc; border-bottom: 1px solid var(--border-color); font-size: 13px; }
.failure { color: var(--color-failed); }
.refresh-error { padding: 7px 20px; color: #8a4b08; background: #fff7e6; border-bottom: 1px solid #ffe1ad; font-size: 12px; }
.tab-bar { display: flex; padding: 0 20px; background: var(--bg-card); border-bottom: 1px solid var(--border-color); }
.tab-btn { background: none; border: none; border-bottom: 2px solid transparent; padding: 11px 20px; cursor: pointer; color: var(--text-secondary); }
.tab-btn.active { color: var(--color-running); border-bottom-color: var(--color-running); font-weight: 600; }
.tab-content { flex: 1; overflow: hidden; }
.notice { padding: 40px; text-align: center; color: var(--text-muted); }
</style>
