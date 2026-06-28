<template>
  <div class="section">
    <header class="section-header">
      <h2>🧪 test_strategy — 测试策略</h2>
    </header>
    <p class="hint">自由对象结构. 常见键: unit / integration / e2e / enforce_tdd / coverage_target</p>
    <div class="strategy-list">
      <div v-for="(val, key) in testStrategy" :key="String(key)" class="strategy-row">
        <span class="s-key">{{ String(key) }}</span>
        <span class="s-val">{{ formatVal(val) }}</span>
        <button class="btn-icon" @click="removeKey(String(key))">×</button>
      </div>
    </div>
    <div class="add-row">
      <input v-model="newKey" class="add-input" placeholder="键名" />
      <input v-model="newValue" class="add-input" placeholder="值 (true/false/数字)" />
      <button class="btn-sm" @click="addKey">+ 新增</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/projectStore'

const store = useProjectStore()
const testStrategy = computed<Record<string, unknown>>(() => store.getSection('test_strategy'))

const newKey = ref('')
const newValue = ref('')

function formatVal(v: unknown): string {
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

function coerceValue(raw: string): unknown {
  if (raw === 'true') return true
  if (raw === 'false') return false
  if (raw === '') return ''
  const n = Number(raw)
  if (!Number.isNaN(n) && raw.trim() !== '') return n
  return raw
}

function addKey() {
  const k = newKey.value.trim()
  if (!k) return
  store.setAt(['test_strategy', k], coerceValue(newValue.value))
  newKey.value = ''
  newValue.value = ''
}

function removeKey(key: string) {
  if (!confirm(`删除 test_strategy.${key}?`)) return
  store.deleteAt(['test_strategy', key])
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.hint { color: #888; font-size: 12px; margin: 0; }
.strategy-list { display: flex; flex-direction: column; gap: 4px; }
.strategy-row {
  display: grid; grid-template-columns: 200px 1fr 24px;
  background: #2d2d2d; padding: 6px 10px; border-radius: 4px;
  align-items: center;
}
.s-key { color: #4fc3f7; font-family: Consolas, monospace; font-size: 12px; }
.s-val { color: #d4d4d4; font-family: Consolas, monospace; font-size: 12px; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.add-row { display: flex; gap: 6px; }
.add-input {
  flex: 1; background: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;
  padding: 4px 8px; border-radius: 4px; font-size: 12px;
  font-family: Consolas, monospace;
}
.add-input:focus { outline: none; border-color: #4fc3f7; }
.btn-sm {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 3px 10px; font-size: 11px; border-radius: 3px;
}
.btn-sm:hover { background: #505050; }
</style>