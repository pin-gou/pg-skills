<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import type { PipelineEvent } from '@/types/pipeline'
import { api } from '@/api/client'

const props = defineProps<{ change: string; refreshKey: number }>()
const events = ref<PipelineEvent[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const error = ref<string | null>(null)
const search = ref('')
const failuresOnly = ref(false)
const expandedLine = ref<number | null>(null)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
let requestVersion = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null

async function loadEvents(silent = false): Promise<void> {
  const version = ++requestVersion
  if (!silent) loading.value = true
  try {
    const response = await api.getEvents(
      props.change,
      page.value,
      pageSize,
      search.value,
      failuresOnly.value,
    )
    if (version !== requestVersion) return
    events.value = response.events
    total.value = response.total
    error.value = null
  } catch (exception) {
    if (version !== requestVersion) return
    error.value = `事件加载失败: ${String(exception)}`
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

function eventCategory(type: string): 'failed' | 'completed' | 'running' | 'other' {
  if (/(fail|error|abandon|escalat)/i.test(type)) return 'failed'
  if (/(completed|received|commit|pass)/i.test(type)) return 'completed'
  if (/(started|dispatch|running|progress)/i.test(type)) return 'running'
  return 'other'
}

function eventColor(type: string): string {
  return { failed: '#F56C6C', completed: '#67C23A', running: '#409EFF', other: '#909399' }[eventCategory(type)]
}

function eventIcon(type: string): string {
  return { failed: 'x', completed: 'v', running: '>', other: '-' }[eventCategory(type)]
}

function formatTimestamp(timestamp: unknown): string {
  if (typeof timestamp !== 'string') return '-'
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString('zh-CN', { hour12: false })
}

function eventField(event: PipelineEvent, key: string): unknown {
  const direct = event[key]
  if (direct !== undefined && direct !== null && direct !== '') return direct
  const data = event.data
  return data && typeof data === 'object' ? (data as Record<string, unknown>)[key] : undefined
}

function eventSummary(event: PipelineEvent): string {
  const value = eventField(event, 'summary') ?? eventField(event, 'status') ?? eventField(event, 'message')
  return value === undefined ? '-' : String(value)
}

function changePage(nextPage: number): void {
  page.value = Math.max(1, Math.min(totalPages.value, nextPage))
  void loadEvents()
}

function toggleEvent(event: PipelineEvent): void {
  const line = event._line || null
  expandedLine.value = expandedLine.value === line ? null : line
}

watch(() => props.refreshKey, () => {
  if (page.value === 1) void loadEvents(true)
})
watch([search, failuresOnly], () => {
  page.value = 1
  if (searchTimer !== null) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchTimer = null
    void loadEvents()
  }, 250)
})
onMounted(() => void loadEvents())
onUnmounted(() => {
  if (searchTimer !== null) clearTimeout(searchTimer)
})
</script>

<template>
  <div class="event-log">
    <div class="toolbar">
      <strong>事件日志（共 {{ total }} 条，最新事件优先）</strong>
      <input v-model="search" placeholder="搜索全部事件、track、phase..." />
      <label><input v-model="failuresOnly" type="checkbox" /> 仅失败</label>
      <button @click="loadEvents()">立即刷新</button>
    </div>
    <div v-if="error" class="error">{{ error }}</div>

    <div class="table-wrap">
      <table>
        <thead><tr><th>时间</th><th>类型</th><th>Track</th><th>Phase</th><th>摘要</th></tr></thead>
        <tbody>
          <template v-for="event in events" :key="event._line">
            <tr class="event-row" @click="toggleEvent(event)">
              <td class="time">{{ formatTimestamp(event.timestamp || event.ts) }}</td>
              <td><span :style="{ color: eventColor(event.type) }">{{ eventIcon(event.type) }} {{ event.type }}</span></td>
              <td>{{ eventField(event, 'track') || '-' }}</td>
              <td>{{ eventField(event, 'phase') || '-' }}</td>
              <td>{{ eventSummary(event) }}</td>
            </tr>
            <tr v-if="expandedLine === event._line" class="event-detail">
              <td colspan="5"><pre>{{ JSON.stringify(event, null, 2) }}</pre></td>
            </tr>
          </template>
          <tr v-if="events.length === 0 && !loading"><td colspan="5" class="empty">没有匹配事件</td></tr>
        </tbody>
      </table>
    </div>

    <div class="pagination">
      <button :disabled="page <= 1" @click="changePage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页</span>
      <button :disabled="page >= totalPages" @click="changePage(page + 1)">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.event-log { height: 100%; display: flex; flex-direction: column; }
.toolbar { display: flex; align-items: center; gap: 14px; padding: 10px 16px; border-bottom: 1px solid var(--border-color); }
.toolbar strong { margin-right: auto; }
.toolbar input[type='text'], .toolbar input:not([type]) { width: 260px; padding: 6px 9px; border: 1px solid var(--border-color); border-radius: 5px; }
.toolbar button, .pagination button { background: white; border: 1px solid var(--border-color); border-radius: 5px; padding: 5px 10px; cursor: pointer; }
.error { padding: 8px 16px; color: var(--color-failed); background: #fff1f0; }
.table-wrap { flex: 1; overflow: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
th { position: sticky; top: 0; background: var(--bg-card); color: var(--text-muted); }
.event-row { cursor: pointer; }
.event-row:hover { background: #f5f7fa; }
.time { width: 180px; color: var(--text-muted); font-family: var(--font-mono); }
.event-detail td { background: #f8fafc; }
.event-detail pre { white-space: pre-wrap; font-size: 12px; }
.empty { text-align: center; color: var(--text-muted); padding: 24px; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 14px; padding: 9px; border-top: 1px solid var(--border-color); }
.pagination button:disabled { opacity: .4; cursor: not-allowed; }
</style>
