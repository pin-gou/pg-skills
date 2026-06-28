<template>
  <div class="section">
    <header class="section-header">
      <h2>📏 coding_standards — 编码规范 (字符串数组)</h2>
    </header>
    <p class="hint">每个元素是一条编码规范字符串. 顺序敏感.</p>
    <ArgsField
      :modelValue="standards"
      @update:modelValue="v => store.setAt(['coding_standards'], v)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import ArgsField from '@/components/fields/ArgsField.vue'

const store = useProjectStore()
const standards = computed<unknown>(() => store.data.coding_standards)
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.hint { color: #888; font-size: 12px; margin: 0; }
</style>