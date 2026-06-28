<template>
  <div class="section">
    <header class="section-header">
      <h2>🌿 git — Git 仓库配置</h2>
    </header>
    <div class="form-grid">
      <FormField name="default_branch" label="default_branch (主分支名)"
        :modelValue="(git as any)?.default_branch"
        :schema="strSchema1"
        @update:modelValue="v => store.setAt(['git', 'default_branch'], v)" />
    </div>
    <p class="hint">pg-verify-and-merge Phase 1/3 用此字段切到目标分支. 通常是 master 或 main.</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const git = computed<unknown>(() => store.data.git)

const strSchema1: JSONSchema7 = {
  type: 'string', minLength: 1,
  description: '主分支名',
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.form-grid { background: #2d2d2d; padding: 16px; border-radius: 6px; }
.hint { color: #888; font-size: 12px; margin: 0; }
</style>