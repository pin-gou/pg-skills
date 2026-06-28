<template>
  <div class="section">
    <header class="section-header">
      <h2>📐 rules — per-artifact 规则 (proposal / design / tasks)</h2>
    </header>
    <p class="hint">自由对象结构, 键为 artifact 名 (proposal/design/tasks), 值为字符串数组</p>
    <div class="rules-block">
      <div v-for="(group, name) in rules" :key="String(name)" class="rule-group">
        <div class="rule-group-header">
          <span class="rg-name">{{ String(name) }}</span>
          <button class="btn-icon" @click="removeGroup(String(name))">×</button>
        </div>
        <ArgsField
          :modelValue="group"
          @update:modelValue="v => store.setAt(['rules', String(name)], v)"
        />
      </div>
      <div class="add-row">
        <input v-model="newName" class="add-input" placeholder="新增 group 名 (proposal/design/tasks)" />
        <button class="btn-sm" :disabled="!newName" @click="addGroup">+ 新增 group</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import ArgsField from '@/components/fields/ArgsField.vue'

const store = useProjectStore()
const rules = computed<Record<string, unknown>>(() => store.getSection('rules'))
const newName = ref('')

function addGroup() {
  if (!newName.value) return
  store.setAt(['rules', newName.value], [])
  newName.value = ''
}

function removeGroup(name: string) {
  if (!confirm(`删除 rules.${name}?`)) return
  store.deleteAt(['rules', name])
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.hint { color: #888; font-size: 12px; margin: 0; }
.rules-block { display: flex; flex-direction: column; gap: 8px; }
.rule-group {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  padding: 10px 14px;
}
.rule-group-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.rg-name { color: #4fc3f7; font-family: Consolas, monospace; font-size: 13px; font-weight: 600; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.add-row { display: flex; gap: 6px; align-items: center; }
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
.btn-sm:disabled { opacity: .4; cursor: not-allowed; }
</style>