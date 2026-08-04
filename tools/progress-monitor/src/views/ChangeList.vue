<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { usePipelineStore } from '@/stores/pipelineStore'
import { STATUS_ICONS, STATUS_COLORS } from '@/types/pipeline'

const router = useRouter()
const store = usePipelineStore()

const snapshotStatusMap = computed(() => {
  const map: Record<string, string> = {}
  for (const c of store.changes) {
    if (c.snapshotStatus) map[c.name] = c.snapshotStatus
  }
  return map
})

function statusColor(status: string | null): string {
  return STATUS_COLORS[status || 'pending'] || STATUS_COLORS.pending
}

function statusIcon(status: string | null): string {
  return STATUS_ICONS[status || 'pending'] || '○'
}

function tracksFromManifest(name: string, change: any): string {
  const m = store.manifest
  if (!m) return '-'
  return '-'
}

function goDetail(name: string) {
  router.push(`/change/${encodeURIComponent(name)}`)
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin} 分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour} 小时前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
</script>

<template>
  <div class="page">
    <header class="page-header">
      <h1>🔄 Pipeline Progress Monitor</h1>
      <span class="port-badge">Port 9323</span>
    </header>

    <div class="page-body">
      <section class="change-section">
        <h2 class="section-title">📂 Active ({{ store.activeChanges.length }})</h2>
        <table class="change-table" v-if="store.activeChanges.length > 0">
          <thead>
            <tr>
              <th class="col-name">变更名称</th>
              <th class="col-status">状态</th>
              <th class="col-mtime">更新时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in store.activeChanges" :key="c.name" class="change-row" @click="goDetail(c.name)">
              <td class="col-name">
                <a class="change-link">{{ c.name }}</a>
              </td>
              <td class="col-status">
                <span class="status-badge" :style="{ color: statusColor(c.snapshotStatus) }">
                  {{ statusIcon(c.snapshotStatus) }} {{ c.snapshotStatus || 'no snapshot' }}
                </span>
              </td>
              <td class="col-mtime">{{ formatTime(c.mtime) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无活跃变更</div>
      </section>

      <section class="change-section">
        <h2 class="section-title">📦 Archived ({{ store.archivedChanges.length }})</h2>
        <table class="change-table" v-if="store.archivedChanges.length > 0">
          <thead>
            <tr>
              <th class="col-name">变更名称</th>
              <th class="col-status">状态</th>
              <th class="col-mtime">更新时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in store.archivedChanges" :key="c.name" class="change-row" @click="goDetail(c.name)">
              <td class="col-name">
                <a class="change-link">{{ c.name }}</a>
              </td>
              <td class="col-status">
                <span class="status-badge" :style="{ color: statusColor(c.snapshotStatus) }">
                  {{ statusIcon(c.snapshotStatus) }} {{ c.snapshotStatus || 'no snapshot' }}
                </span>
              </td>
              <td class="col-mtime">{{ formatTime(c.mtime) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无归档变更</div>
      </section>
    </div>

    <footer class="legend">
      <span class="legend-item"><span style="color:var(--color-completed)">✓</span> Completed</span>
      <span class="legend-item"><span style="color:var(--color-running)">●</span> Running</span>
      <span class="legend-item"><span style="color:var(--color-failed)">✗</span> Failed</span>
      <span class="legend-item"><span style="color:var(--color-pending)">○</span> Pending</span>
    </footer>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; }
.page-header {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 24px; background: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
}
.page-header h1 { font-size: 20px; font-weight: 600; }
.port-badge {
  font-size: 12px; padding: 2px 8px;
  background: #ecf5ff; color: var(--color-running);
  border-radius: 4px; font-family: var(--font-mono);
}
.page-body { flex: 1; overflow: auto; padding: 20px 24px; }
.section-title { font-size: 16px; font-weight: 600; margin-bottom: 12px; color: var(--text-secondary); }
.change-section { margin-bottom: 32px; }
.change-table { width: 100%; border-collapse: collapse; }
.change-table th {
  text-align: left; padding: 10px 16px;
  font-size: 12px; font-weight: 500; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 2px solid var(--border-color);
}
.change-table td { padding: 12px 16px; border-bottom: 1px solid var(--border-color); }
.change-row { cursor: pointer; transition: background 0.15s; }
.change-row:hover { background: #f0f5ff; }
.change-link { font-weight: 500; }
.col-name { width: 50%; }
.col-status { width: 30%; }
.col-mtime { width: 20%; color: var(--text-muted); font-size: 13px; }
.status-badge { font-weight: 500; font-size: 14px; }
.empty { color: var(--text-muted); padding: 24px 0; text-align: center; }
.legend {
  display: flex; gap: 20px; padding: 10px 24px;
  background: var(--bg-card); border-top: 1px solid var(--border-color);
  font-size: 13px; color: var(--text-secondary);
}
</style>