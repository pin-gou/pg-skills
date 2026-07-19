<template>
  <div class="section">
    <header class="section-header">
      <h2>🛤 tracks — TDV 循环编排</h2>
      <div class="section-actions">
        <button class="btn-sm" @click="addTrack">+ 新增 track</button>
      </div>
    </header>

    <div class="track-list">
      <details
        v-for="(track, name) in tracks"
        :key="String(name)"
        :open="store.selection.track === name"
        class="track-card"
        :class="{ active: store.selection.track === name }"
        @toggle="onToggle($event, String(name))"
      >
        <summary class="track-summary" @click="store.selectTrack(String(name))">
          <span class="t-name">{{ String(name) }}</span>
          <span class="t-type">{{ (track as any).type || 'standard' }}</span>
          <span class="t-modules">{{ modulesText((track as any).modules) }}</span>
          <button class="btn-icon" @click.stop="removeTrack(String(name))">×</button>
        </summary>

        <div class="track-form" @click.stop>
          <div class="row-2">
            <FormField name="type" label="track 类型" :modelValue="(track as any).type"
              :schema="typeSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'type'], v)" />
            <FormField name="review_level" label="review_level" :modelValue="(track as any).review_level"
              :schema="reviewSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'review_level'], v)" />
          </div>

          <FormField name="description" label="description"
            :modelValue="(track as any).description"
            :schema="strSchema"
            @update:modelValue="v => store.setAt(['tracks', String(name), 'description'], v)" />

          <FormField name="modules" label="modules (可写 module 列表)"
            :modelValue="(track as any).modules"
            :schema="modulesSchema"
            @update:modelValue="v => store.setAt(['tracks', String(name), 'modules'], v)" />

          <h4 class="block-title">commands (仅 type=simple 生效)</h4>
          <div v-if="(track as any).commands" class="cmd-list">
            <div v-for="(cmd, idx) in (track as any).commands" :key="idx" class="cmd-item">
              <div class="cmd-header">
                <span class="cmd-num">#{{ idx + 1 }}</span>
                <button class="btn-icon" @click="removeCmd(String(name), idx)">×</button>
              </div>
              <FormField :name="`cmd-${idx}`" label="cmd"
                :modelValue="(cmd as any).cmd ?? cmd"
                :schema="cmdSchema" :required="true"
                @update:modelValue="v => updateCmd(String(name), idx, v)" />
              <div v-if="typeof cmd === 'object'" class="cmd-obj-fields">
                <div class="row-3">
                  <FormField :name="`to-${idx}`" label="timeout_seconds"
                    :modelValue="(cmd as any).timeout_seconds"
                    :schema="timeoutSchema"
                    @update:modelValue="v => updateCmdField(String(name), idx, 'timeout_seconds', v)" />
                  <FormField :name="`of-${idx}`" label="on_failure"
                    :modelValue="(cmd as any).on_failure"
                    :schema="onFailureSchema"
                    @update:modelValue="v => updateCmdField(String(name), idx, 'on_failure', v)" />
                  <FormField :name="`rm-${idx}`" label="retry_max"
                    :modelValue="(cmd as any).retry_max"
                    :schema="retrySchema"
                    @update:modelValue="v => updateCmdField(String(name), idx, 'retry_max', v)" />
                </div>
                <FormField :name="`rts-${idx}`" label="retry_timeout_seconds"
                  :modelValue="(cmd as any).retry_timeout_seconds"
                  :schema="timeoutSchema"
                  @update:modelValue="v => updateCmdField(String(name), idx, 'retry_timeout_seconds', v)" />
              </div>
              <button v-if="typeof cmd === 'string'" class="btn-sm" @click="expandCmd(String(name), idx)">
                → 展开为对象
              </button>
            </div>
          </div>
          <button class="btn-sm" @click="addCmd(String(name))">+ 新增 command</button>

          <h4 class="block-title">simple track defaults</h4>
          <div class="row-3">
            <FormField name="timeout_seconds" label="timeout_seconds"
              :modelValue="(track as any).timeout_seconds"
              :schema="timeoutNullableSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'timeout_seconds'], v)" />
          </div>
          <div class="row-3">
            <FormField name="max_fix_retries" label="max_fix_retries"
              :modelValue="(track as any).max_fix_retries"
              :schema="retriesSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'max_fix_retries'], v)" />
            <FormField name="max_fail_retries" label="max_fail_retries"
              :modelValue="(track as any).max_fail_retries"
              :schema="retriesSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'max_fail_retries'], v)" />
            <FormField name="max_gate_fix_retries" label="max_gate_fix_retries"
              :modelValue="(track as any).max_gate_fix_retries"
              :schema="retriesSchema"
              @update:modelValue="v => store.setAt(['tracks', String(name), 'max_gate_fix_retries'], v)" />
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import type { JSONSchema7 } from '@/types/schema'

const store = useProjectStore()
const tracks = computed<Record<string, unknown>>(() => store.getSection('tracks'))

const strSchema: JSONSchema7 = { type: 'string', multiline: true }
const typeSchema: JSONSchema7 = {
  type: 'string', enum: ['standard', 'simple'], default: 'standard',
  description: 'standard=完整 TDVG 序列; simple=直接执行 commands',
}
const reviewSchema: JSONSchema7 = {
  type: 'string', enum: ['none', 'standard', 'security'], default: 'standard',
}
const modulesSchema: JSONSchema7 = {
  type: 'array', items: { type: 'string' },
  description: '可写 module 列表 (引用 modules.<id>)',
}
const cmdSchema: JSONSchema7 = {
  type: 'string', minLength: 1, description: 'shell 命令',
}
const timeoutSchema: JSONSchema7 = {
  type: 'integer', minimum: 1, description: '命令超时(秒)',
}
const timeoutNullableSchema: JSONSchema7 = {
  type: ['integer', 'null'], minimum: 1, default: 1800,
  description: 'simple track 默认命令超时(秒)',
}
const onFailureSchema: JSONSchema7 = {
  type: 'string', enum: ['fail', 'continue', 'retry'], default: 'fail',
  description: '单条命令失败处置',
}
const retrySchema: JSONSchema7 = {
  type: 'integer', minimum: 1, default: 2, description: 'on_failure=retry 时的最大重试次数',
}
const retriesSchema: JSONSchema7 = {
  type: 'integer', minimum: 1, description: '重试次数',
}

function modulesText(arr: string[] | undefined): string {
  if (!arr || arr.length === 0) return '(no modules)'
  return arr.join(', ')
}

function onToggle(e: Event, name: string) {
  if ((e.target as HTMLDetailsElement).open) {
    store.selectTrack(name)
  }
}

function addTrack() {
  const name = prompt('track 名称 (如: my-track):')
  if (!name) return
  store.setAt(['tracks', name], {
    modules: [],
    description: '',
  })
  store.selectTrack(name)
}

function removeTrack(name: string) {
  if (!confirm(`删除 track "${name}"?`)) return
  store.deleteAt(['tracks', name])
}

function addCmd(name: string) {
  const cur = (store.getAt(['tracks', name, 'commands']) as unknown[]) || []
  store.setAt(['tracks', name, 'commands'], [...cur, ''])
}

function removeCmd(name: string, idx: number) {
  if (!confirm(`删除 command #${idx + 1}?`)) return
  store.deleteAt(['tracks', name, 'commands', idx])
}

function updateCmd(name: string, idx: number, v: unknown) {
  const cur = (store.getAt(['tracks', name, 'commands']) as unknown[]) || []
  const next = [...cur]
  next[idx] = v
  store.setAt(['tracks', name, 'commands'], next)
}

function updateCmdField(name: string, idx: number, key: string, v: unknown) {
  const cur = (store.getAt(['tracks', name, 'commands']) as unknown[]) || []
  const next = [...cur]
  const existing = next[idx]
  if (typeof existing === 'object' && existing !== null) {
    const obj = { ...(existing as Record<string, unknown>) }
    if (v === undefined || v === null || v === '') {
      delete obj[key]
    } else {
      obj[key] = v
    }
    next[idx] = obj
  } else {
    next[idx] = { cmd: typeof existing === 'string' ? existing : '', [key]: v }
  }
  store.setAt(['tracks', name, 'commands'], next)
}

function expandCmd(name: string, idx: number) {
  const cur = (store.getAt(['tracks', name, 'commands']) as unknown[]) || []
  const next = [...cur]
  next[idx] = { cmd: typeof next[idx] === 'string' ? next[idx] as string : '' }
  store.setAt(['tracks', name, 'commands'], next)
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
.track-list { display: flex; flex-direction: column; gap: 8px; }
.track-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  overflow: hidden;
}
.track-card.active { border-color: #4fc3f7; }
.track-summary {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; cursor: pointer; user-select: none;
}
.track-summary:hover { background: #333; }
.track-card[open] .track-summary { background: #2a2a2a; border-bottom: 1px solid #3c3c3c; }
.t-name { font-weight: 600; color: #e0e0e0; min-width: 120px; }
.t-type {
  background: #6a1b9a; color: #e1bee7; font-size: 11px;
  padding: 2px 8px; border-radius: 3px;
}
.t-modules { color: #888; font-size: 11px; flex: 1; font-family: Consolas, monospace; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.track-form { padding: 16px; display: flex; flex-direction: column; gap: 4px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.block-title {
  margin: 16px 0 8px; font-size: 13px; color: #4fc3f7;
  border-bottom: 1px dashed #3c3c3c; padding-bottom: 4px;
}
.cmd-list { display: flex; flex-direction: column; gap: 8px; }
.cmd-item {
  background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px;
  padding: 10px;
}
.cmd-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.cmd-num { color: #4fc3f7; font-family: Consolas, monospace; font-size: 12px; font-weight: 600; }
.cmd-obj-fields { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
</style>