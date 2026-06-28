<template>
  <div class="command-field">
    <div class="cmd-tabs">
      <button
        type="button"
        class="tab"
        :class="{ active: mode === 'string' }"
        @click="setMode('string')"
      >字符串</button>
      <button
        type="button"
        class="tab"
        :class="{ active: mode === 'object' }"
        @click="setMode('object')"
      >对象</button>
    </div>
    <input
      v-if="mode === 'string'"
      type="text"
      class="cmd-input"
      :value="modelValue ?? ''"
      :placeholder="placeholder"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
    <div v-else class="cmd-object">
      <input
        type="text"
        class="cmd-input"
        :value="cmdValue"
        placeholder="shell 命令"
        @input="updateCmd(($event.target as HTMLInputElement).value)"
      />
      <NumberField
        :modelValue="timeoutValue"
        :schema="timeoutSchema"
        @update:modelValue="updateTimeout"
      />
    </div>
    <div v-if="description" class="cmd-hint">{{ description }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import NumberField from './NumberField.vue'
import type { JSONSchema7 } from '@/types/schema'

const props = defineProps<{
  modelValue: unknown
  placeholder?: string
  description?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [v: unknown] }>()

const timeoutSchema: JSONSchema7 = { type: 'integer', minimum: 1, description: '命令超时(秒)' }

const mode = ref<'string' | 'object'>('string')

watch(() => props.modelValue, (v) => {
  if (typeof v === 'string') mode.value = 'string'
  else if (v && typeof v === 'object') mode.value = 'object'
}, { immediate: true })

const cmdValue = computed(() => {
  const v = props.modelValue
  if (typeof v === 'object' && v !== null) {
    return (v as Record<string, unknown>).cmd ?? ''
  }
  return ''
})

const timeoutValue = computed(() => {
  const v = props.modelValue
  if (typeof v === 'object' && v !== null) {
    return (v as Record<string, unknown>).timeout_seconds ?? null
  }
  return null
})

function setMode(m: 'string' | 'object') {
  if (m === mode.value) return
  if (m === 'string') {
    const obj = props.modelValue as Record<string, unknown> | null
    const cmd = obj?.cmd ?? ''
    mode.value = 'string'
    emit('update:modelValue', cmd)
  } else {
    const s = typeof props.modelValue === 'string' ? props.modelValue : ''
    mode.value = 'object'
    emit('update:modelValue', { cmd: s })
  }
}

function updateCmd(v: string) {
  const obj = (typeof props.modelValue === 'object' && props.modelValue !== null
    ? { ...(props.modelValue as Record<string, unknown>) }
    : {}) as Record<string, unknown>
  obj.cmd = v
  emit('update:modelValue', obj)
}

function updateTimeout(v: number | null) {
  const obj = (typeof props.modelValue === 'object' && props.modelValue !== null
    ? { ...(props.modelValue as Record<string, unknown>) }
    : {}) as Record<string, unknown>
  obj.timeout_seconds = v ?? undefined
  emit('update:modelValue', obj)
}
</script>

<style scoped>
.command-field { display: flex; flex-direction: column; gap: 6px; }
.cmd-tabs { display: flex; gap: 4px; }
.tab {
  background: #2d2d2d; border: 1px solid #3c3c3c; color: #aaa;
  padding: 3px 10px; font-size: 11px; border-radius: 3px;
}
.tab.active { background: #1565c0; border-color: #1976d2; color: #fff; }
.cmd-input {
  width: 100%; background: #1e1e1e; border: 1px solid #3c3c3c;
  color: #d4d4d4; padding: 6px 10px; border-radius: 4px;
  font-size: 12px; font-family: Consolas, monospace;
}
.cmd-input:focus { outline: none; border-color: #4fc3f7; }
.cmd-object { display: grid; grid-template-columns: 1fr 120px; gap: 6px; }
.cmd-hint { font-size: 11px; color: #888; }
</style>