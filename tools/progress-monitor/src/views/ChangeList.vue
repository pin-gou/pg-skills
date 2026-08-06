<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePipelineStore } from '@/stores/pipelineStore'
import { usePolling } from '@/composables/usePolling'
import { STATUS_COLORS, STATUS_ICONS } from '@/types/pipeline'
import type { ChangeInfo } from '@/types/pipeline'

const router = useRouter()
const store = usePipelineStore()
const { start } = usePolling(() => store.loadChanges(store.changes.length > 0), 5000)

function statusColor(status: string | null): string {
  return STATUS_COLORS[status || 'pending'] || STATUS_COLORS.pending
}

function statusIcon(status: string | null): string {
  return STATUS_ICONS[status || 'pending'] || '○'
}

function location(change: ChangeInfo): string {
  return [change.currentStage, change.currentTrack, change.currentPhase].filter(Boolean).join(' / ') || '-'
}

function progress(change: ChangeInfo): string {
  return change.totalPhases > 0 ? `${change.completedPhases}/${change.totalPhases}` : '-'
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const date = new Date(iso)
  const minutes = Math.floor((Date.now() - date.getTime()) / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`
  return date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(start)
</script>

<template>
  <div class="page">
    <header><h1>Pipeline Progress Monitor</h1><span>只读 · 5 秒刷新</span></header>
    <div v-if="store.error" class="error">{{ store.error }}</div>
    <main>
      <div v-if="store.changesLoading && store.changes.length === 0" class="empty">加载中...</div>
      <template v-else>
      <section v-for="section in [{ title: 'Active', rows: store.activeChanges }, { title: 'Archived', rows: store.archivedChanges }]" :key="section.title">
        <h2>{{ section.title }}（{{ section.rows.length }}）</h2>
        <table v-if="section.rows.length">
          <thead><tr><th>变更</th><th>状态</th><th>当前位置</th><th>阶段进度</th><th>最后活动</th></tr></thead>
          <tbody>
            <tr v-for="change in section.rows" :key="change.name" @click="router.push(`/change/${encodeURIComponent(change.name)}`)">
              <td><strong>{{ change.name }}</strong><div v-if="change.parseError" class="error-text">snapshot 解析失败</div></td>
              <td>
                <span :style="{ color: statusColor(change.snapshotStatus) }">{{ statusIcon(change.snapshotStatus) }} {{ change.snapshotStatus || 'no snapshot' }}</span>
                <span v-if="change.isStalled" class="stalled">疑似停滞</span>
              </td>
              <td>{{ location(change) }}</td>
              <td>{{ progress(change) }}</td>
              <td>{{ formatTime(change.lastEventAt || change.mtime) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无记录</div>
      </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.page { height: 100%; overflow: auto; }
header { display: flex; align-items: baseline; gap: 12px; padding: 16px 24px; background: white; border-bottom: 1px solid var(--border-color); }
header h1 { font-size: 20px; }
header span { color: var(--text-muted); font-size: 12px; }
main { padding: 20px 24px; }
section { margin-bottom: 30px; }
h2 { margin-bottom: 10px; font-size: 16px; color: var(--text-secondary); }
table { width: 100%; border-collapse: collapse; background: white; }
th, td { text-align: left; padding: 11px 13px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
th { color: var(--text-muted); font-size: 12px; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: #f5f9ff; }
.stalled { margin-left: 8px; padding: 2px 6px; color: #8a4b08; background: #fff2d5; border-radius: 4px; font-size: 11px; }
.error, .error-text { color: var(--color-failed); }
.error { padding: 8px 24px; background: #fff1f0; }
.error-text { font-size: 11px; margin-top: 3px; }
.empty { padding: 20px; text-align: center; color: var(--text-muted); background: white; }
</style>
