<template>
  <label class="boolean-field">
    <input
      type="checkbox"
      :checked="!!modelValue"
      @change="emit('update:modelValue', ($event.target as HTMLInputElement).checked)"
    />
    <span>{{ !!modelValue ? 'true' : 'false' }}</span>
  </label>
</template>

<script setup lang="ts">
defineProps<{ modelValue: unknown }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean] }>()
</script>

<style scoped>
.boolean-field {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: #d4d4d4;
}
.boolean-field input { cursor: pointer; }
</style>