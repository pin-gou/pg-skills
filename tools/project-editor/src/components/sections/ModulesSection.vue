<template>
  <div class="section">
    <header class="section-header">
      <h2>📦 modules — 代码模块与 build/lint/test 命令</h2>
      <div class="section-actions">
        <select v-model="templateId" class="template-select">
          <option value="">从预设新增 ▾</option>
          <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.label }}</option>
        </select>
        <button class="btn-sm" :disabled="!templateId" @click="addFromTemplate">+ 新增 module</button>
        <button class="btn-sm" @click="addModule">+ 空模块</button>
      </div>
    </header>

    <div class="module-list">
      <details
        v-for="(mod, name) in modules"
        :key="String(name)"
        :open="store.selection.module === name"
        class="module-card"
        :class="{ active: store.selection.module === name }"
        @toggle="onToggle($event, String(name))"
      >
        <summary class="module-summary" @click="store.selectModule(String(name))">
          <span class="m-name">{{ String(name) }}</span>
          <span class="m-lang">{{ (mod as any).language }}</span>
          <span class="m-root">{{ shortRoot((mod as any).root) }}</span>
          <button class="btn-icon" @click.stop="removeModule(String(name))">×</button>
        </summary>

        <div class="module-form" @click.stop>
          <div class="row-2">
            <FormField name="root" label="Root 路径" :modelValue="(mod as any).root"
              :schema="rootSchema" :required="true"
              @update:modelValue="v => store.setAt(['modules', String(name), 'root'], v)" />
            <FormField name="language" label="编程语言" :modelValue="(mod as any).language"
              :schema="langSchema" :required="true"
              @update:modelValue="v => store.setAt(['modules', String(name), 'language'], v)" />
          </div>
          <div class="row-2">
            <FormField name="timeout_seconds" label="超时(秒)" :modelValue="(mod as any).timeout_seconds"
              :schema="timeoutSchema"
              @update:modelValue="v => store.setAt(['modules', String(name), 'timeout_seconds'], v)" />
            <FormField name="review_level" label="Review 级别" :modelValue="(mod as any).review_level"
              :schema="reviewSchema"
              @update:modelValue="v => store.setAt(['modules', String(name), 'review_level'], v)" />
          </div>
          <FormField name="description" label="描述" :modelValue="(mod as any).description"
            :schema="strSchema"
            @update:modelValue="v => store.setAt(['modules', String(name), 'description'], v)" />

          <h4 class="cmd-title">build</h4>
          <CommandField
            :modelValue="(mod as any).build"
            placeholder="cd webvirt-xxx && mvn install"
            description="模块构建命令"
            @update:modelValue="v => store.setAt(['modules', String(name), 'build'], v)"
          />

          <h4 class="cmd-title">lint</h4>
          <CommandField
            :modelValue="(mod as any).lint"
            placeholder="cd webvirt-xxx && mvn checkstyle:check"
            description="模块静态检查命令"
            @update:modelValue="v => store.setAt(['modules', String(name), 'lint'], v)"
          />

          <h4 class="cmd-title">test</h4>
          <div v-if="(mod as any).test" class="test-list">
            <div v-for="(cmd, key) in (mod as any).test" :key="String(key)" class="test-row">
              <div class="test-key">{{ String(key) }}</div>
              <div class="test-cmd">
                <CommandField
                  :modelValue="cmd"
                  :placeholder="`${String(key)} 测试命令`"
                  @update:modelValue="v => store.setAt(['modules', String(name), 'test', String(key)], v)"
                />
              </div>
              <button class="btn-icon" @click="removeTest(String(name), String(key))">×</button>
            </div>
          </div>
          <div class="add-test-row">
            <input v-model="newTestKey" class="test-input" placeholder="test key (unit/integration/e2e/...)" />
            <button class="btn-sm" :disabled="!newTestKey" @click="addTestKey(String(name))">+ 新增 test</button>
          </div>

          <div class="module-actions">
            <button class="btn-sm" @click="copyModule(String(name))">📋 复制此 module</button>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import CommandField from '@/components/fields/CommandField.vue'
import { getTemplate, listTemplates } from '@/templates/modules'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const modules = computed<Record<string, unknown>>(() => store.getSection('modules'))

const templates = listTemplates()
const templateId = ref('')
const newTestKey = ref('')

const rootSchema: JSONSchema7 = { type: 'string', minLength: 1, description: '模块代码根路径, 相对项目根' }
const langSchema: JSONSchema7 = {
  type: 'string', enum: ['java', 'go', 'typescript', 'python', 'proto', 'shell'],
  description: '模块编程语言',
}
const timeoutSchema: JSONSchema7 = {
  type: 'integer', minimum: 1, default: 1800,
  description: '命令默认超时(秒). 命令级 timeout_seconds 覆盖此值',
}
const reviewSchema: JSONSchema7 = {
  type: 'string', enum: ['none', 'standard', 'security'], default: 'standard',
  description: 'review 严格度',
}
const strSchema: JSONSchema7 = { type: 'string', description: '文本描述' }

function shortRoot(root: string | undefined): string {
  if (!root) return ''
  return root.length > 24 ? root.slice(0, 22) + '…' : root
}

function onToggle(e: Event, name: string) {
  if ((e.target as HTMLDetailsElement).open) {
    store.selectModule(name)
  }
}

function addModule() {
  const name = prompt('模块名称 (如: new-module):')
  if (!name) return
  store.setAt(['modules', name], { root: `webvirt-${name}`, language: 'typescript' })
  store.selectModule(name)
}

function addFromTemplate() {
  if (!templateId.value) return
  const tpl = getTemplate(templateId.value)
  if (!tpl) return
  const name = prompt('模块名称 (如: my-module):')
  if (!name) return
  store.setAt(['modules', name], { ...tpl })
  store.selectModule(name)
  templateId.value = ''
}

function copyModule(name: string) {
  const newName = prompt(`复制 "${name}" 为:`, `${name}-copy`)
  if (!newName || newName === name) return
  const src = store.getAt(['modules', name]) as Record<string, unknown> | undefined
  if (!src) return
  store.setAt(['modules', newName], JSON.parse(JSON.stringify(src)))
  store.selectModule(newName)
}

function removeModule(name: string) {
  if (!confirm(`删除模块 "${name}"?`)) return
  store.deleteAt(['modules', name])
}

function addTestKey(name: string) {
  const k = newTestKey.value.trim()
  if (!k) return
  store.setAt(['modules', name, 'test', k], '')
  newTestKey.value = ''
}

function removeTest(name: string, key: string) {
  if (!confirm(`删除 test.${key}?`)) return
  store.deleteAt(['modules', name, 'test', key])
}
</script>

<style scoped>
.section { display: flex; flex-direction: column; gap: 12px; }
.section-header {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 8px; border-bottom: 1px solid #3c3c3c;
}
.section-header h2 { margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600; }
.section-actions { display: flex; gap: 6px; align-items: center; }
.template-select {
  background: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;
  padding: 4px 8px; border-radius: 4px; font-size: 12px;
}
.btn-sm {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 3px 10px; font-size: 11px; border-radius: 3px;
}
.btn-sm:hover { background: #505050; }
.btn-sm:disabled { opacity: .4; cursor: not-allowed; }
.module-list { display: flex; flex-direction: column; gap: 8px; }
.module-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  overflow: hidden;
}
.module-card.active { border-color: #4fc3f7; }
.module-summary {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; cursor: pointer; user-select: none;
}
.module-summary:hover { background: #333; }
.module-card[open] .module-summary { background: #2a2a2a; border-bottom: 1px solid #3c3c3c; }
.m-name { font-weight: 600; color: #e0e0e0; min-width: 120px; }
.m-lang {
  background: #1565c0; color: #bbdefb; font-size: 11px;
  padding: 2px 8px; border-radius: 3px;
}
.m-root {
  background: #37474f; color: #90caf9; font-size: 11px;
  padding: 2px 8px; border-radius: 3px; font-family: Consolas, monospace;
  flex: 1;
}
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.module-form { padding: 16px; display: flex; flex-direction: column; gap: 4px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.cmd-title {
  margin: 12px 0 4px; font-size: 12px; color: #aaa;
  font-family: Consolas, monospace; font-weight: 600;
}
.test-list { display: flex; flex-direction: column; gap: 8px; }
.test-row { display: grid; grid-template-columns: 90px 1fr 22px; gap: 6px; align-items: start; }
.test-key {
  font-family: Consolas, monospace; font-size: 11px; color: #4fc3f7;
  padding: 6px 0; font-weight: 600;
}
.test-cmd { display: flex; flex-direction: column; }
.add-test-row { display: flex; gap: 6px; margin-top: 8px; align-items: center; }
.test-input {
  flex: 1; background: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;
  padding: 4px 8px; border-radius: 4px; font-size: 12px;
  font-family: Consolas, monospace;
}
.test-input:focus { outline: none; border-color: #4fc3f7; }
.module-actions {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid #3c3c3c;
}
</style>