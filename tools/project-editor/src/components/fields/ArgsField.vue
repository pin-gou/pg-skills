<template>
  <div class="args-field">
    <div v-for="(item, idx) in items" :key="idx" class="arg-row">
      <input
        type="text"
        class="arg-input"
        :value="item"
        @input="update(idx, ($event.target as HTMLInputElement).value)"
      />
      <button type="button" class="arg-del" @click="remove(idx)">×</button>
    </div>
    <div class="arg-toolbar">
      <button type="button" class="btn-sm" @click="add">+ 添加参数</button>
      <span class="hint">支持模板: {'{role}'} / {'{instance.name}'} / {'{instance.host}'} / {'{lines:100}'}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ modelValue: unknown }>()
const emit = defineEmits<{ 'update:modelValue': [v: string[]] }>()

const items = computed<string[]>(() => {
  if (Array.isArray(props.modelValue)) return props.modelValue as string[]
  return []
})

function add() {
  emit('update:modelValue', [...items.value, ''])
}

function update(idx: number, v: string) {
  const next = [...items.value]
  next[idx] = v
  emit('update:modelValue', next)
}

function remove(idx: number) {
  const next = items.value.filter((_, i) => i !== idx)
  emit('update:modelValue', next)
}
</script>

<style scoped>
.args-field { display: flex; flex-direction: column; gap: 4px; }
.arg-row { display: flex; gap: 4px; align-items: center; }
.arg-input {
  flex: 1; background: #1e1e1e; border: 1px solid #3c3c3c;
  color: #d4d4d4; padding: 4px 8px; border-radius: 4px;
  font-size: 12px; font-family: Consolas, monospace;
}
.arg-input:focus { outline: none; border-color: #4fc3f7; }
.arg-del {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  width: 22px; height: 22px; border-radius: 3px; font-size: 13px;
}
.arg-del:hover { background: #b71c1c; }
.arg-toolbar { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.btn-sm {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 2px 8px; font-size: 11px; border-radius: 3px;
}
.btn-sm:hover { background: #505050; }
.hint { font-size: 10px; color: #777; font-family: Consolas, monospace; }
</style>