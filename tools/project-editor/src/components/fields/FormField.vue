<template>
  <div class="form-field" :class="{ invalid: !!invalid }">
    <label v-if="label || name" class="field-label">
      <span class="field-name">{{ label || name }}</span>
      <FieldTooltip v-if="schema?.description" :description="schema.description" />
      <span v-if="required" class="required">*</span>
    </label>
    <component
      :is="resolvedComponent"
      :modelValue="modelValue"
      :schema="schema"
      v-bind="$attrs"
      @update:modelValue="(v: unknown) => emit('update:modelValue', v)"
    />
    <div v-if="invalid" class="field-error">⚠ {{ invalid }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { JSONSchema7 } from '@/types/schema'
import FieldTooltip from '@/components/shared/FieldTooltip.vue'
import StringField from './StringField.vue'
import TextField from './TextField.vue'
import NumberField from './NumberField.vue'
import EnumField from './EnumField.vue'
import BooleanField from './BooleanField.vue'
import CommandField from './CommandField.vue'
import ArgsField from './ArgsField.vue'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  name: string
  label?: string
  modelValue: unknown
  schema?: JSONSchema7
  required?: boolean
  invalid?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [v: unknown] }>()

const resolvedComponent = computed(() => {
  const s = props.schema
  if (!s) return StringField
  if (s.enum) return EnumField
  if (s.oneOf || s.anyOf) return TextField
  if (s.type === 'boolean') return BooleanField
  if (s.type === 'integer' || s.type === 'number') return NumberField
  if (s.type === 'array') {
    if (s.items && (s.items as JSONSchema7).type === 'string') return ArgsField
    return TextField
  }
  if (s.type === 'string' && (s as JSONSchema7 & { multiline?: boolean }).multiline) return TextField
  if (s.type === 'string' && (s.minLength === undefined || (s.minLength as number) > 80)) {
    return TextField
  }
  return StringField
})
</script>

<style scoped>
.form-field { margin-bottom: 12px; }
.form-field.invalid .field-name { color: #ef5350; }
.field-label {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: #aaa; margin-bottom: 4px;
}
.field-name { text-transform: none; }
.required { color: #ef5350; font-size: 14px; }
.field-error {
  font-size: 11px; color: #ef5350; margin-top: 2px;
}
</style>