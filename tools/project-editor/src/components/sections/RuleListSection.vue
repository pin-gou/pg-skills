<template>
  <div class="section">
    <header class="section-header">
      <h2>📝 {{ title }}</h2>
      <div class="section-actions">
        <button class="btn-sm" @click="addItem">+ 新增 {{ itemLabel }}</button>
      </div>
    </header>
    <p v-if="hint" class="hint">{{ hint }}</p>

    <div class="item-list">
      <div v-for="(item, idx) in items" :key="idx" class="item-card">
        <div class="item-header">
          <span class="item-num">#{{ idx + 1 }}</span>
          <span class="item-id">{{ (item as any).id || '(unnamed)' }}</span>
          <button class="btn-icon" @click="removeItem(idx)">×</button>
        </div>
        <div class="item-form">
          <div class="row-3">
            <FormField :name="`id-${idx}`" label="id"
              :modelValue="(item as any).id"
              :schema="strSchema1"
              @update:modelValue="v => updateField(idx, 'id', v)" />
            <FormField :name="`type-${idx}`" label="type"
              :modelValue="(item as any).type"
              :schema="typeSchema"
              @update:modelValue="v => updateField(idx, 'type', v)" />
            <FormField :name="`target-${idx}`" label="target_agent"
              :modelValue="(item as any).target_agent"
              :schema="agentSchema"
              @update:modelValue="v => updateField(idx, 'target_agent', v)" />
          </div>
          <FormField :name="`pos-${idx}`" label="position"
            :modelValue="(item as any).position"
            :schema="positionSchema"
            @update:modelValue="v => updateField(idx, 'position', v)" />
          <FormField :name="`tpl-${idx}`" label="template (多行文本)"
            :modelValue="(item as any).template"
            :schema="tplSchema"
            @update:modelValue="v => updateField(idx, 'template', v)" />
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

const props = defineProps<{
  title: string
  sectionKey: string
  itemLabel: string
  hint?: string
}>()

const store = useProjectStore()
const items = computed<unknown[]>(() => {
  return (store.getSection(props.sectionKey) as unknown[]) || []
})

const strSchema1: JSONSchema7 = { type: 'string', minLength: 1, description: '必填' }
const typeSchema: JSONSchema7 = {
  type: 'string', description: '注入类型 (inject-prompt 等)',
}
const agentSchema: JSONSchema7 = {
  type: 'string',
  enum: ['pg-build/dev', 'pg-build/test', 'pg-build/verify', 'pg-build/gate', 'pg-build/fix', 'pg-build/fix-gate', 'pg-fix-issue/executor'],
  description: '注入到哪个 agent',
}
const positionSchema: JSONSchema7 = {
  type: 'string', enum: ['prepend', 'append'], default: 'prepend',
}
const tplSchema: JSONSchema7 = {
  type: 'string', multiline: true, description: '模板文本',
}

function updateField(idx: number, key: string, value: unknown) {
  const cur = items.value.map((it: any) => ({ ...it }))
  if (value === undefined || value === null || value === '') {
    delete cur[idx][key]
  } else {
    cur[idx][key] = value
  }
  store.setAt([props.sectionKey], cur)
}

function addItem() {
  const cur = items.value.map((it: any) => ({ ...it }))
  cur.push({ id: `${props.sectionKey}_new`, type: 'inject-prompt', target_agent: 'pg-build/dev', position: 'prepend', template: '' })
  store.setAt([props.sectionKey], cur)
}

function removeItem(idx: number) {
  if (!confirm(`删除 ${props.itemLabel} #${idx + 1}?`)) return
  store.deleteAt([props.sectionKey, idx])
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
.hint { color: #888; font-size: 12px; margin: 0; }
.btn-sm {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 3px 10px; font-size: 11px; border-radius: 3px;
}
.btn-sm:hover { background: #505050; }
.item-list { display: flex; flex-direction: column; gap: 8px; }
.item-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  overflow: hidden;
}
.item-header {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px; background: #2a2a2a;
  border-bottom: 1px solid #3c3c3c;
}
.item-num {
  background: #455a64; color: #cfd8dc; font-size: 11px;
  padding: 2px 8px; border-radius: 3px; font-weight: 600;
}
.item-id { font-family: Consolas, monospace; color: #4fc3f7; font-size: 12px; flex: 1; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.item-form { padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
</style>