<template>
  <details class="top-schema-section">
    <summary>⚙ 顶级参数 (schema / $schema)</summary>
    <div class="content">
      <FormField name="schema" label="schema 版本"
        :modelValue="(store.data as any).schema"
        :schema="schemaField"
        @update:modelValue="v => store.setAt(['schema'], v)" />
      <FormField name="$schema" label="$schema (JSON Schema URL 或相对路径)"
        :modelValue="(store.data as any)['$schema']"
        :schema="refSchema"
        @update:modelValue="v => store.setAt(['$schema'], v)" />
    </div>
  </details>
</template>

<script setup lang="ts">
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()

const schemaField: JSONSchema7 = {
  type: 'string', enum: ['spec-driven'], default: 'spec-driven',
  description: '配置 schema 版本, 当前固定为 spec-driven',
}
const refSchema: JSONSchema7 = {
  type: 'string', description: '指向本 schema 文件, IDE/编辑器用于自动补全与校验',
}
</script>

<style scoped>
.top-schema-section {
  background: #2d2d2d; border: 1px solid #3c3c3c;
  border-radius: 6px; padding: 8px 14px;
  margin-bottom: 16px;
}
.top-schema-section summary {
  cursor: pointer; font-size: 12px; color: #888; user-select: none;
}
.top-schema-section summary:hover { color: #d4d4d4; }
.content { padding-top: 8px; }
</style>