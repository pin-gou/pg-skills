<template>
  <div class="section">
    <header class="section-header">
      <h2>🗄 flyway — 数据库迁移路径</h2>
    </header>
    <div class="form-grid">
      <FormField name="migration_path" label="migration_path (相对项目根)"
        :modelValue="(flyway as any)?.migration_path"
        :schema="strSchema1"
        @update:modelValue="v => store.setAt(['flyway', 'migration_path'], v)" />
    </div>
    <p class="hint">pg-verify-and-merge Phase 0 用此路径做 migration 重编号.</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const flyway = computed<unknown>(() => store.data.flyway)

const strSchema1: JSONSchema7 = {
  type: 'string', minLength: 1,
  description: 'Flyway migration 目录, 相对项目根',
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