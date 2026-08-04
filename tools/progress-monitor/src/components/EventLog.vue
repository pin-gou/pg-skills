<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import type { PipelineEvent, EventsResponse } from '@/types/pipeline'
import { EVENT_TYPE_COLORS } from '@/types/pipeline'
import { api } from '@/api/client'

const props = defineProps<{ change: string }>()

const events = ref<PipelineEvent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)

const totalPages = ref(0)

async function loadEvents() {
  loading.value = true
  try {
    const res = await api.getEvents(props.change, page.value, pageSize)
    events.value = res.events
    total.value = res.total
    totalPages.value = Math.max(1, Math.ceil(res.total / pageSize))
  } catch {
    events.value = []
    total.value = 0
    totalPages.value = 0
  } finally {
    loading.value = false
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value--
    loadEvents()
  }
}

function nextPage() {
  if (page.value < totalPages.value) {
    page.value++
    loadEvents()
  }
}

function eventColor(type: string): string {
  return EVENT_TYPE_COLORS[type] || '#909399'
}

function eventIcon(type: string): string {
  if (type.includes('failed') || type.includes('abandoned')) return '🔴'
  if (type.includes('completed') || type.includes('received')) return '🟢'
  if (type.includes('started')) return '🔵'
  if (type.includes('_commit')) return '⚪'
  return '⚪'
}

function formatTimestamp(ts: string | undefined): string {
  if (!ts) return '-'
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts
  }
}

onMounted(loadEvents)
</script>

<template>
  <div class="event-log">
    <div class="toolbar">
      <span class="title">事件日志 (共 {{ total }} 条)</span>
    </div>

    <div class="table-wrap">
      <table class="event-table">
        <thead>
          <tr>
            <th class="col-time">时间</th>
            <th class="col-type">事件类型</th>
            <th class="col-track">Track</th>
            <th class="col-phase">Phase</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="e._line || Math.random()" class="event-row">
            <td class="col-time">{{ formatTimestamp(e.timestamp as string) }}</td>
            <td class="col-type">
              <span class="event-type-badge" :style="{ color: eventColor(e.type) }">
                {{ eventIcon(e.type) }} {{ e.type }}
              </span>
            </td>
            <td class="col-track">{{ e.track || '-' }}</td>
            <td class="col-phase">{{ e.phase || '-' }}</td>
          </tr>
          <tr v-if="events.length === 0 && !loading">
            <td colspan="4" class="empty">暂无事件</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pagination" v-if="totalPages > 1">
      <button :disabled="page <= 1" @click="prevPage">◀ 上一页</button>
      <span class="page-info">第 {{ page }} / {{ totalPages }} 页</span>
      <button :disabled="page >= totalPages" @click="nextPage">下一页 ▶</button>
    </div>

    <div class="legend">
      <span>🟢 完成</span>
      <span>🔵 开始</span>
      <span>🔴 失败</span>
      <span>⚪ 其他</span>
    </div>
  </div>
</template>

<style scoped>
.event-log { height: 100%; display: flex; flex-direction: column; }
.toolbar {
  display: flex; align-items: center; padding: 10px 16px;
  border-bottom: 1px solid var(--border-color); flex-shrink: 0;
}
.title { font-weight: 600; font-size: 14px; }
.table-wrap { flex: 1; overflow: auto; }
.event-table { width: 100%; border-collapse: collapse; }
.event-table th {
  text-align: left; padding: 10px 16px;
  font-size: 12px; font-weight: 500; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 0.5px;
  border-bottom: 2px solid var(--border-color); position: sticky; top: 0;
  background: var(--bg-card);
}
.event-table td { padding: 8px 16px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
.event-row:hover { background: #f5f7fa; }
.col-time { width: 15%; font-family: var(--font-mono); color: var(--text-muted); }
.col-type { width: 35%; }
.col-track { width: 25%; }
.col-phase { width: 25%; }
.event-type-badge { font-weight: 500; font-size: 13px; }
.empty { text-align: center; color: var(--text-muted); padding: 24px; }
.pagination {
  display: flex; align-items: center; justify-content: center; gap: 16px;
  padding: 10px 16px; border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}
.pagination button {
  background: none; border: 1px solid var(--border-color);
  border-radius: 4px; padding: 6px 14px; cursor: pointer;
  font-size: 13px; color: var(--text-secondary);
}
.pagination button:hover:not(:disabled) { border-color: var(--color-running); color: var(--color-running); }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.page-info { font-size: 13px; color: var(--text-muted); }
.legend {
  display: flex; gap: 16px; padding: 6px 16px;
  border-top: 1px solid var(--border-color);
  font-size: 12px; color: var(--text-muted); flex-shrink: 0;
}
</style>