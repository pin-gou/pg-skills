<template>
  <select
    class="enum-field"
    :value="modelValue ?? ''"
    @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option value="" disabled>— 选择 —</option>
    <option v-for="opt in options" :key="opt" :value="opt">{{ opt }}</option>
  </select>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { JSONSchema7 } from '@/types/schema'

const props = defineProps<{ modelValue: unknown; schema?: JSONSchema7 }>()
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()

const options = computed<string[]>(() => {
  return (props.schema?.enum as string[]) || []
})
</script>

<style scoped>
.enum-field {
  width: 100%;
  background: #1e1e1e;
  border: 1px solid #3c3c3c;
  color: #d4d4d4;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 13px;
}
.enum-field:focus { outline: none; border-color: #4fc3f7; }
</style>