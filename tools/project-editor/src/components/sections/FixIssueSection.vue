<template>
  <div class="section">
    <header class="section-header">
      <h2>🔧 fix_issue — pg-fix-issue SKILL 专用配置</h2>
    </header>

    <div class="form-grid">
      <FormField name="max_iteration_count" label="主 agent 整体迭代上限"
        :modelValue="(fixIssue as any)?.max_iteration_count"
        :schema="intSchema1"
        @update:modelValue="v => store.setAt(['fix_issue', 'max_iteration_count'], v)" />
      <FormField name="partial_success_threshold" label="部分成功率阈值 (0=禁用)"
        :modelValue="(fixIssue as any)?.partial_success_threshold"
        :schema="numSchema"
        @update:modelValue="v => store.setAt(['fix_issue', 'partial_success_threshold'], v)" />
      <FormField name="ask_environment_choice" label="Phase 3 询问用户选 environment"
        :modelValue="(fixIssue as any)?.ask_environment_choice"
        :schema="boolSchema"
        @update:modelValue="v => store.setAt(['fix_issue', 'ask_environment_choice'], v)" />
      <FormField name="ask_prepare_env" label="Phase 3 询问是否执行 prepare_env"
        :modelValue="(fixIssue as any)?.ask_prepare_env"
        :schema="boolSchema"
        @update:modelValue="v => store.setAt(['fix_issue', 'ask_prepare_env'], v)" />
      <FormField name="ask_clean_env" label="Phase 3 询问是否执行 clean_env"
        :modelValue="(fixIssue as any)?.ask_clean_env"
        :schema="boolSchema"
        @update:modelValue="v => store.setAt(['fix_issue', 'ask_clean_env'], v)" />
      <FormField name="allow_manual_verification" label="允许手动验证"
        :modelValue="(fixIssue as any)?.allow_manual_verification"
        :schema="boolSchema"
        @update:modelValue="v => store.setAt(['fix_issue', 'allow_manual_verification'], v)" />

      <div class="full-width">
        <h4 class="block-title">escalation_artifacts</h4>
        <div class="artifacts-list">
          <label v-for="opt in artifactOptions" :key="opt" class="artifact-item">
            <input type="checkbox"
              :checked="((fixIssue as any)?.escalation_artifacts || []).includes(opt)"
              @change="toggleArtifact(opt, ($event.target as HTMLInputElement).checked)" />
            <span>{{ opt }}</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const fixIssue = computed<unknown>(() => store.data.fix_issue)

const artifactOptions = ['diag_logs', 'call_chain_analysis', 'phase2_output', 'executor_json_history', 'git_diff_state']

const intSchema1: JSONSchema7 = {
  type: 'integer', minimum: 1, default: 5,
  description: '正整数, 默认 5',
}
const numSchema: JSONSchema7 = {
  type: 'number', minimum: 0, maximum: 1, default: 0.7,
  description: '0~1 之间的小数, 0 表示禁用',
}
const boolSchema: JSONSchema7 = {
  type: 'boolean', default: true,
  description: 'true/false',
}

function toggleArtifact(name: string, checked: boolean) {
  const cur = ((fixIssue.value as any)?.escalation_artifacts || []) as string[]
  const next = checked ? [...cur, name] : cur.filter(x => x !== name)
  store.setAt(['fix_issue', 'escalation_artifacts'], next.length > 0 ? next : undefined)
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.form-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px;
  background: #2d2d2d; padding: 16px; border-radius: 6px;
}
.full-width { grid-column: 1 / -1; }
.block-title {
  margin: 8px 0 8px; font-size: 12px; color: #4fc3f7; font-weight: 600;
}
.artifacts-list { display: flex; flex-wrap: wrap; gap: 8px; }
.artifact-item {
  display: inline-flex; align-items: center; gap: 4px;
  background: #1e1e1e; padding: 4px 10px; border-radius: 4px;
  font-size: 12px; cursor: pointer; font-family: Consolas, monospace;
}
.artifact-item input { cursor: pointer; }
</style>