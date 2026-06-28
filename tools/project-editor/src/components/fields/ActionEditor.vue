<template>
  <div class="action-editor">
    <div v-if="!modelValue || typeof modelValue !== 'object'" class="empty">
      <em>该 action 未配置 (host/hosts 二选一必填)</em>
    </div>
    <template v-else>
      <div class="row-2">
        <FormField name="host" label="host"
          :modelValue="(modelValue as any).host"
          :schema="hostSchema"
          @update:modelValue="updateField('host', $event)" />
        <FormField name="hosts" label="hosts (多 host 数组或模板)"
          :modelValue="(modelValue as any).hosts"
          :schema="hostsSchema"
          @update:modelValue="updateField('hosts', $event)" />
      </div>
      <FormField name="parallel" label="parallel"
        :modelValue="(modelValue as any).parallel"
        :schema="parallelSchema"
        @update:modelValue="updateField('parallel', $event)" />
      <FormField name="script" label="script"
        :modelValue="(modelValue as any).script"
        :schema="scriptSchema"
        @update:modelValue="updateField('script', $event)" />
      <FormField name="args" label="args"
        :modelValue="(modelValue as any).args"
        :schema="argsSchema"
        @update:modelValue="updateField('args', $event)" />
      <FormField name="timeout_seconds" label="timeout_seconds"
        :modelValue="(modelValue as any).timeout_seconds"
        :schema="timeoutSchema"
        @update:modelValue="updateField('timeout_seconds', $event)" />
      <FormField name="description" label="description"
        :modelValue="(modelValue as any).description"
        :schema="descSchema"
        @update:modelValue="updateField('description', $event)" />
    </template>
  </div>
</template>

<script setup lang="ts">
import FormField from './FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const props = defineProps<{
  modelValue: unknown
  label?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [v: unknown] }>()

const hostSchema: JSONSchema7 = {
  type: 'string', minLength: 1,
  description: '单 host. 支持模板 {instance.host}',
}
const hostsSchema: JSONSchema7 = {
  type: 'string',
  description: '多 host 简写. 若 hosts 本身是数组请改用 array 形态 (留空)',
}
const parallelSchema: JSONSchema7 = {
  type: 'boolean', default: false,
  description: 'hosts 列表下是否并行执行',
}
const scriptSchema: JSONSchema7 = {
  type: 'string', multiline: true,
  description: '执行的脚本路径或命令',
}
const argsSchema: JSONSchema7 = {
  type: 'array', items: { type: 'string' },
  description: '脚本参数. 支持模板 {role} / {instance.name} / {instance.host}',
}
const timeoutSchema: JSONSchema7 = {
  type: 'integer', minimum: 1,
  description: '执行超时(秒)',
}
const descSchema: JSONSchema7 = {
  type: 'string', multiline: true,
  description: '动作语义说明, 仅供 LLM 阅读',
}

function updateField(key: string, value: unknown) {
  const obj = (typeof props.modelValue === 'object' && props.modelValue !== null
    ? { ...(props.modelValue as Record<string, unknown>) }
    : {}) as Record<string, unknown>
  if (value === undefined || value === null || value === '') {
    delete obj[key]
  } else {
    obj[key] = value
  }
  emit('update:modelValue', obj)
}
</script>

<style scoped>
.action-editor { display: flex; flex-direction: column; gap: 4px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.empty { color: #888; font-size: 12px; padding: 6px 0; }
</style>