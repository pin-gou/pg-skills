<script setup lang="ts">
import { onMounted } from 'vue'
import { usePipelineStore } from '@/stores/pipelineStore'

const store = usePipelineStore()

onMounted(() => {
  store.loadChanges()
})
</script>

<template>
  <div id="app-root">
    <router-view />
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app, #app-root { height: 100%; width: 100%; overflow: hidden; }

:root {
  --bg-primary: #f5f7fa;
  --bg-card: #ffffff;
  --bg-sidebar: #ffffff;
  --text-primary: #303133;
  --text-secondary: #606266;
  --text-muted: #909399;
  --border-color: #e4e7ed;
  --color-pending: #909399;
  --color-running: #409EFF;
  --color-completed: #67C23A;
  --color-failed: #F56C6C;
  --color-escalate: #E6A23C;
  --color-skipped: #C0C4CC;
  --radius: 8px;
  --shadow: 0 1px 4px rgba(0,0,0,0.08);
  --font-mono: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 14px;
  color: var(--text-primary);
  background: var(--bg-primary);
}

a { color: var(--color-running); text-decoration: none; }
a:hover { text-decoration: underline; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: #c0c4cc; border-radius: 3px; }
::-webkit-scrollbar-track { background: transparent; }
</style>