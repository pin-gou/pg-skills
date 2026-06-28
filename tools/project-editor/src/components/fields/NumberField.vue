<template>
  <input
    type="number"
    class="number-field"
    :value="modelValue ?? ''"
    :min="schema?.minimum ?? undefined"
    :max="schema?.maximum ?? undefined"
    @input="onInput"
  />
</template>

<script setup lang="ts">
import type { JSONSchema7 } from '@/types/schema'

const props = defineProps<{ modelValue: unknown; schema?: JSONSchema7 }>()
const emit = defineEmits<{ 'update:modelValue': [v: number | null] }>()

function onInput(e: Event) {
  const t = e.target as HTMLInputElement
  const v = t.value === '' ? null : Number(t.value)
  emit('update:modelValue', v)
}
</script>

<style scoped>
.number-field {
  width: 100%;
  background: #1e1e1e;
  border: 1px solid #3c3c3c;
  color: #d4d4d4;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 13px;
}
.number-field:focus { outline: none; border-color: #4fc3f7; }
</style>