<template>
  <div class="section">
    <header class="section-header">
      <h2>✅ verify_merge — pg-verify-and-merge 配置</h2>
    </header>
    <div class="form-grid">
      <FormField name="skip_tests_if_no_conflict" label="merge 无冲突时跳过 Phase 2 全部测试"
        :modelValue="(verifyMerge as any)?.skip_tests_if_no_conflict"
        :schema="boolSchema"
        @update:modelValue="v => store.setAt(['verify_merge', 'skip_tests_if_no_conflict'], v)" />
    </div>
    <p class="hint">当 merge 无冲突时, 是否跳过 Phase 2 全部测试以加速合并. 默认 true.</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const verifyMerge = computed<unknown>(() => store.data.verify_merge)

const boolSchema: JSONSchema7 = {
  type: 'boolean', default: true,
  description: 'true=跳过; false=仍跑测试',
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.form-grid {
  background: #2d2d2d; padding: 16px; border-radius: 6px;
}
.hint { color: #888; font-size: 12px; margin: 0; }
</style>