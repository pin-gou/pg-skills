<template>
  <header class="topbar">
    <div class="topbar-left">
      <span class="logo">📋</span>
      <span class="title">.pg/project.yaml 编辑器</span>
    </div>
    <nav class="topbar-tabs">
      <button
        v-for="t in tabs"
        :key="t.id"
        class="tab"
        :class="{ active: store.view === t.id }"
        @click="store.setView(t.id)"
      >{{ t.icon }} {{ t.label }}</button>
    </nav>
    <div class="topbar-actions">
      <span v-if="store.loading" class="badge loading">加载中...</span>
      <span v-else-if="store.errorMessage" class="badge error" :title="store.errorMessage">⚠ 错误</span>
      <template v-else>
        <span class="badge" :class="store.isValid ? 'ok' : 'error'">
          {{ store.isValid ? '✓ Pass' : `✗ ${store.errors.length}` }}
        </span>
        <button class="btn" @click="store.reload()">↻ 重载</button>
        <button
          class="btn primary"
          :disabled="!store.dirty || !store.isValid"
          :title="!store.isValid ? 'schema 校验未通过' : '保存 (Ctrl+S)'"
          @click="handleSaveClick"
        >💾 保存</button>
      </template>
    </div>
  </header>
</template>

<script setup lang="ts">
import { useProjectStore, type ViewMode } from '@/stores/projectStore'

const store = useProjectStore()
const emit = defineEmits<{ openDiff: [] }>()

const tabs: Array<{ id: ViewMode; label: string; icon: string }> = [
  { id: 'dashboard', label: '仪表板', icon: '📊' },
  { id: 'form', label: '表单', icon: '📋' },
  { id: 'canvas', label: '画布', icon: '📐' },
]

function handleSaveClick() {
  emit('openDiff')
}
</script>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 48px;
  padding: 0 16px;
  background: #2d2d2d;
  border-bottom: 1px solid #3c3c3c;
  flex-shrink: 0;
  gap: 16px;
}
.topbar-left { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.logo { font-size: 18px; }
.title { font-size: 14px; font-weight: 600; color: #e0e0e0; }
.topbar-tabs { display: flex; gap: 4px; flex: 1; justify-content: center; }
.tab {
  background: transparent; border: 1px solid transparent; color: #aaa;
  padding: 4px 14px; font-size: 13px; border-radius: 4px;
  transition: all .15s;
}
.tab:hover { background: #3c3c3c; color: #d4d4d4; }
.tab.active { background: #1565c0; border-color: #1976d2; color: #fff; }
.topbar-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.badge {
  font-size: 12px; padding: 2px 10px; border-radius: 10px;
  background: #3c3c3c; color: #aaa;
}
.badge.ok { background: #1b5e20; color: #a5d6a7; }
.badge.error { background: #b71c1c; color: #ffcdd2; }
.badge.loading { background: #37474f; color: #90caf9; }
.btn {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 4px 14px; border-radius: 4px;
  font-size: 12px; transition: background .15s;
}
.btn:hover { background: #505050; }
.btn.primary { background: #1565c0; border-color: #1976d2; }
.btn.primary:hover { background: #1976d2; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
</style>