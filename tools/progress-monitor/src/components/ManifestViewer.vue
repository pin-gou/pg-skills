<script setup lang="ts">
import { ref, computed } from 'vue'
import { usePipelineStore } from '@/stores/pipelineStore'
import { stringify } from 'yaml'

const store = usePipelineStore()

const yamlText = computed(() => {
  if (!store.manifest) return '# 暂无 manifest 数据'
  try {
    return stringify(store.manifest, { indent: 2, lineWidth: 120, sortMapEntries: false })
  } catch {
    return JSON.stringify(store.manifest, null, 2)
  }
})

const collapsed = ref(false)
</script>

<template>
  <div class="manifest-viewer">
    <div class="toolbar">
      <span class="title">execution-manifest.yaml</span>
      <button class="collapse-btn" @click="collapsed = !collapsed">
        {{ collapsed ? '展开' : '折叠' }}
      </button>
    </div>
    <div v-if="!collapsed" class="yaml-content">
      <pre class="yaml-text">{{ yamlText }}</pre>
    </div>
  </div>
</template>

<style scoped>
.manifest-viewer { height: 100%; display: flex; flex-direction: column; }
.toolbar {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px; border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.title { font-weight: 600; font-size: 14px; flex: 1; }
.collapse-btn {
  background: none; border: 1px solid var(--border-color);
  border-radius: 4px; padding: 4px 12px; cursor: pointer;
  font-size: 12px; color: var(--text-secondary);
}
.collapse-btn:hover { border-color: var(--color-running); color: var(--color-running); }
.yaml-content { flex: 1; overflow: auto; padding: 16px; }
.yaml-text {
  white-space: pre-wrap; font-family: var(--font-mono);
  font-size: 13px; line-height: 1.5; margin: 0;
  color: var(--text-primary);
}
</style>