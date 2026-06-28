<template>
  <div class="section">
    <header class="section-header">
      <h2>⏱ stages — 阶段编排 (按顺序执行)</h2>
      <div class="section-actions">
        <button class="btn-sm" @click="addStage">+ 新增 stage</button>
      </div>
    </header>

    <div class="stage-list">
      <div
        v-for="(stage, idx) in stages"
        :key="idx"
        class="stage-card"
        :class="{ active: store.selection.stage === (stage as any).name }"
        @click="store.selectStage((stage as any).name)"
      >
        <div class="stage-header">
          <span class="stage-idx">{{ idx + 1 }}</span>
          <span class="s-name">{{ (stage as any).name }}</span>
          <span class="s-tracks">{{ (stage as any).tracks.join(' → ') }}</span>
          <span class="s-gate">{{ (stage as any).gate || 'all_pass' }}</span>
          <span v-if="(stage as any).environment" class="s-env">
            env {{ (stage as any).environment.required === false ? 'no' : 'yes' }}
          </span>
          <button class="btn-icon" @click.stop="removeStage(idx)">×</button>
        </div>

        <details class="stage-details" open>
          <summary>详情</summary>
          <div class="stage-form">
            <div class="row-2">
              <FormField :name="`name-${idx}`" label="name" :modelValue="(stage as any).name"
                :schema="nameSchema" :required="true"
                @update:modelValue="v => updateStage(idx, 'name', v)" />
              <FormField :name="`gate-${idx}`" label="gate" :modelValue="(stage as any).gate"
                :schema="gateSchema"
                @update:modelValue="v => updateStage(idx, 'gate', v)" />
            </div>
            <div class="row-2">
              <FormField :name="`test_key-${idx}`" label="test_key"
                :modelValue="(stage as any).test_key"
                :schema="testKeySchema"
                @update:modelValue="v => updateStage(idx, 'test_key', v)" />
              <FormField :name="`required-${idx}`" label="environment.required"
                :modelValue="(stage as any).environment?.required"
                :schema="boolSchema"
                @update:modelValue="v => updateEnvField(idx, 'required', v)" />
            </div>
            <FormField :name="`tracks-${idx}`" label="tracks (按列表顺序执行)"
              :modelValue="(stage as any).tracks"
              :schema="tracksSchema" :required="true"
              @update:modelValue="v => updateStage(idx, 'tracks', v)" />
            <FormField :name="`desc-${idx}`" label="description"
              :modelValue="(stage as any).description"
              :schema="descSchema"
              @update:modelValue="v => updateStage(idx, 'description', v)" />

            <h5 class="sub-title">environment.selection_rules (自然语言)</h5>
            <ArgsField
              :modelValue="(stage as any).environment?.selection_rules"
              @update:modelValue="v => updateEnvField(idx, 'selection_rules', v)"
            />

            <h5 class="sub-title">on_conditions (本 stage 启用条件)</h5>
            <ArgsField
              :modelValue="(stage as any).on_conditions"
              @update:modelValue="v => updateStage(idx, 'on_conditions', v)"
            />

            <div class="stage-order">
              <button class="btn-sm" :disabled="idx === 0" @click.stop="moveStage(idx, -1)">↑ 上移</button>
              <button class="btn-sm" :disabled="idx === stages.length - 1" @click.stop="moveStage(idx, 1)">↓ 下移</button>
            </div>
          </div>
        </details>
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
const stages = computed<unknown[]>(() => (store.data.stages as unknown[]) || [])

const nameSchema: JSONSchema7 = {
  type: 'string', pattern: '^[a-z][a-z0-9-]*$', minLength: 1,
  description: '阶段名',
}
const gateSchema: JSONSchema7 = {
  type: 'string', enum: ['all_pass', 'any_pass', 'no_gate'], default: 'all_pass',
  description: '阶段 gate 策略',
}
const testKeySchema: JSONSchema7 = {
  type: 'string', default: 'unit',
  description: '标准化值: unit / integration / e2e',
}
const boolSchema: JSONSchema7 = {
  type: 'boolean', default: true,
  description: 'true=runner 启停环境; false=纯单元测试',
}
const tracksSchema: JSONSchema7 = {
  type: 'array', items: { type: 'string' },
  description: '阶段内顺序执行的 track 列表 (引用 tracks.<id>)',
}
const descSchema: JSONSchema7 = { type: 'string', multiline: true }

function updateStage(idx: number, key: string, value: unknown) {
  const cur = stages.value.map((s: any) => ({ ...s }))
  if (value === undefined || value === null || value === '') {
    delete cur[idx][key]
  } else {
    cur[idx][key] = value
  }
  store.setAt(['stages'], cur)
}

function updateEnvField(idx: number, key: string, value: unknown) {
  const cur = stages.value.map((s: any) => ({ ...s }))
  const env = { ...(cur[idx].environment || {}) }
  if (value === undefined || value === null || value === '') {
    delete env[key]
  } else {
    env[key] = value
  }
  cur[idx].environment = env
  store.setAt(['stages'], cur)
}

function addStage() {
  const name = prompt('stage 名称 (如: dev):')
  if (!name) return
  const cur = stages.value.map((s: any) => ({ ...s }))
  cur.push({
    name,
    tracks: [],
    test_key: 'unit',
    gate: 'all_pass',
    environment: { required: true },
  })
  store.setAt(['stages'], cur)
  store.selectStage(name)
}

function removeStage(idx: number) {
  if (!confirm(`删除 stage #${idx + 1}?`)) return
  store.deleteAt(['stages', idx])
}

function moveStage(idx: number, dir: number) {
  const cur = stages.value.map((s: any) => ({ ...s }))
  const target = idx + dir
  if (target < 0 || target >= cur.length) return
  ;[cur[idx], cur[target]] = [cur[target], cur[idx]]
  store.setAt(['stages'], cur)
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
.btn-sm:disabled { opacity: .4; cursor: not-allowed; }
.stage-list { display: flex; flex-direction: column; gap: 6px; }
.stage-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  cursor: pointer;
}
.stage-card.active { border-color: #4fc3f7; }
.stage-header {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px;
}
.stage-header:hover { background: #333; }
.stage-idx {
  background: #455a64; color: #cfd8dc; font-size: 11px;
  width: 24px; height: 24px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 600;
}
.s-name { font-weight: 600; color: #e0e0e0; min-width: 100px; }
.s-tracks { color: #888; font-size: 11px; flex: 1; font-family: Consolas, monospace; }
.s-gate {
  background: #37474f; color: #90caf9; font-size: 10px;
  padding: 2px 6px; border-radius: 3px;
}
.s-env {
  background: #2e7d32; color: #c8e6c9; font-size: 10px;
  padding: 2px 6px; border-radius: 3px;
}
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.stage-details { border-top: 1px solid #3c3c3c; padding: 0 14px; }
.stage-details summary {
  cursor: pointer; padding: 6px 0; font-size: 12px; color: #888;
}
.stage-details summary:hover { color: #d4d4d4; }
.stage-form { padding: 8px 0 16px; display: flex; flex-direction: column; gap: 4px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.sub-title {
  margin: 12px 0 6px; font-size: 11px; color: #aaa;
  font-family: Consolas, monospace; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
}
.stage-order { display: flex; gap: 6px; margin-top: 12px; }
</style>