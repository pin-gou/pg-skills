<template>
  <textarea
    class="text-field"
    :value="String(modelValue ?? '')"
    :rows="String(props.rows ?? 3)"
    :placeholder="String(props.placeholder ?? '')"
    @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  />
</template>

<script setup lang="ts">
const props = withDefaults(defineProps<{ modelValue: unknown; rows?: number; placeholder?: string }>(), {
  rows: 3,
  placeholder: '',
})
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()
</script>

<style scoped>
.text-field {
  width: 100%;
  background: #1e1e1e;
  border: 1px solid #3c3c3c;
  color: #d4d4d4;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-family: Consolas, 'Courier New', monospace;
  resize: vertical;
}
.text-field:focus { outline: none; border-color: #4fc3f7; }
</style>