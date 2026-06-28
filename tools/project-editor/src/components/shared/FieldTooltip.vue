<template>
  <span class="field-tooltip" :title="description">ⓘ</span>
</template>

<script setup lang="ts">
defineProps<{ description: string }>()
</script>

<style scoped>
.field-tooltip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 11px;
  color: #4fc3f7;
  cursor: help;
  border-radius: 50%;
  background: rgba(79, 195, 247, 0.1);
}
.field-tooltip:hover { background: rgba(79, 195, 247, 0.25); }
</style>