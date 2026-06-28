<template>
  <input
    type="text"
    class="string-field"
    :value="modelValue ?? ''"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>

<script setup lang="ts">
defineProps<{ modelValue: unknown }>()
const emit = defineEmits<{ 'update:modelValue': [v: string] }>()
</script>

<style scoped>
.string-field {
  width: 100%;
  background: #1e1e1e;
  border: 1px solid #3c3c3c;
  color: #d4d4d4;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 13px;
}
.string-field:focus { outline: none; border-color: #4fc3f7; }
</style>