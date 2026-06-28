<template>
  <footer class="statusbar">
    <span class="status-item" :class="{ warn: store.dirty }">
      <template v-if="store.dirty">
        ⚠ 未保存 {{ store.dirtyCount.add + store.dirtyCount.del + store.dirtyCount.mod }} 处
      </template>
      <template v-else>
        ✓ 已保存
      </template>
    </span>
    <span class="status-item" :class="{ ok: store.isValid, error: !store.isValid }">
      {{ store.isValid ? '✓ schema 校验通过' : `✗ schema 校验 ${store.errors.length} 错误` }}
    </span>
    <span class="status-item">Ctrl+S 保存</span>
    <span v-if="firstError" class="status-item error-msg">⚠ {{ firstError }}</span>
  </footer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'

const store = useProjectStore()
const firstError = computed(() => store.errors[0]?.message || '')
</script>

<style scoped>
.statusbar {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 28px;
  padding: 0 16px;
  background: #252526;
  border-top: 1px solid #3c3c3c;
  font-size: 11px;
  color: #888;
  flex-shrink: 0;
}
.status-item.warn { color: #ffb74d; }
.status-item.ok { color: #81c784; }
.status-item.error { color: #ef5350; }
.status-item.error-msg {
  margin-left: auto;
  color: #ffb74d;
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 50%;
}
</style>