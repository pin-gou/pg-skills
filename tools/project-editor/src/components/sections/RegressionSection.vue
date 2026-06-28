<template>
  <div class="section">
    <header class="section-header">
      <h2>📊 regression — pg-regression 测试套件配置</h2>
      <div class="section-actions">
        <button class="btn-sm" @click="addSuite">+ 新增 suite</button>
      </div>
    </header>

    <div class="suite-list">
      <div
        v-for="(suite, name) in suites"
        :key="String(name)"
        class="suite-card"
      >
        <div class="suite-header">
          <span class="suite-name">{{ String(name) }}</span>
          <span class="suite-meta">{{ moduleText(suite as any) }}</span>
          <button class="btn-icon" @click="removeSuite(String(name))">×</button>
        </div>

        <div class="suite-form">
          <div class="row-3">
            <FormField :name="`module-${name}`" label="module (被测模块)"
              :modelValue="(suite as any).module"
              :schema="strSchema1" :required="true"
              @update:modelValue="v => store.setAt(['regression', 'suite', String(name), 'module'], v)" />
            <FormField :name="`env-${name}`" label="environment.name"
              :modelValue="(suite as any).environment?.name"
              :schema="strSchema1" :required="true"
              @update:modelValue="v => updateEnvField(String(name), 'name', v)" />
            <FormField :name="`fmt-${name}`" label="output_format"
              :modelValue="(suite as any).output_format"
              :schema="formatSchema"
              @update:modelValue="v => store.setAt(['regression', 'suite', String(name), 'output_format'], v)" />
          </div>

          <h5 class="sub-title">test_keys (要跑的测试类型)</h5>
          <ArgsField
            :modelValue="(suite as any).test_keys"
            @update:modelValue="v => store.setAt(['regression', 'suite', String(name), 'test_keys'], v)"
          />

          <h5 class="sub-title">environment.required_roles (启哪些 service role)</h5>
          <ArgsField
            :modelValue="(suite as any).environment?.required_roles"
            @update:modelValue="v => updateEnvField(String(name), 'required_roles', v)"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import ArgsField from '@/components/fields/ArgsField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const suites = computed<Record<string, unknown>>(() => {
  const reg = store.data.regression as any
  return (reg?.suite || {}) as Record<string, unknown>
})

const strSchema1: JSONSchema7 = { type: 'string', minLength: 1 }
const formatSchema: JSONSchema7 = {
  type: 'string',
  enum: ['maven-surefire', 'playwright', 'go-test', 'shell'],
  description: '测试结果解析器格式. 缺省按 language + test_key 推断',
}

function moduleText(suite: any): string {
  const m = suite?.module
  const keys = (suite?.test_keys || []).join('+')
  return `${m} [${keys}]`
}

function updateEnvField(suiteName: string, key: string, value: unknown) {
  const cur = store.getAt(['regression', 'suite', suiteName, 'environment']) as any || {}
  const next = { ...cur }
  if (value === undefined || value === null || value === '') {
    delete next[key]
  } else {
    next[key] = value
  }
  store.setAt(['regression', 'suite', suiteName, 'environment'],
    Object.keys(next).length > 0 ? next : undefined)
}

function addSuite() {
  const name = prompt('suite 名称 (如: backend / frontend / agent):')
  if (!name) return
  store.setAt(['regression', 'suite', name], {
    module: name,
    test_keys: ['unit'],
    environment: {
      name: 'dev-local',
      required_roles: [],
    },
  })
}

function removeSuite(name: string) {
  if (!confirm(`删除 suite "${name}"?`)) return
  store.deleteAt(['regression', 'suite', name])
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.section-actions { display: flex; gap: 6px; }
.btn-sm {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 3px 10px; font-size: 11px; border-radius: 3px;
}
.btn-sm:hover { background: #505050; }
.suite-list { display: flex; flex-direction: column; gap: 8px; }
.suite-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  overflow: hidden;
}
.suite-header {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: #2a2a2a;
  border-bottom: 1px solid #3c3c3c;
}
.suite-name { font-weight: 600; color: #e0e0e0; min-width: 100px; }
.suite-meta { color: #888; font-size: 12px; flex: 1; font-family: Consolas, monospace; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.suite-form { padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.sub-title {
  margin: 8px 0 4px; font-size: 11px; color: #aaa;
  font-family: Consolas, monospace; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
}
</style>