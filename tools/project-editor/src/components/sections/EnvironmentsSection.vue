<template>
  <div class="section">
    <header class="section-header">
      <h2>🌐 environments — 运行时拓扑 + 启停/验活动作</h2>
      <div class="section-actions">
        <button class="btn-sm" @click="addEnvironment">+ 新增 environment</button>
      </div>
    </header>

    <div class="env-list">
      <details
        v-for="(env, name) in environments"
        :key="String(name)"
        :open="store.selection.env === name"
        class="env-card"
        :class="{ active: store.selection.env === name }"
        @toggle="onToggle($event, String(name))"
      >
        <summary class="env-summary" @click="store.selectEnv(String(name))">
          <span class="e-name">{{ String(name) }}</span>
          <span class="e-meta">{{ rolesList(env as any) }}</span>
          <button class="btn-icon" @click.stop="removeEnvironment(String(name))">×</button>
        </summary>

        <div class="env-form" @click.stop>
          <FormField name="description" label="描述" :modelValue="(env as any).description"
            :schema="strSchema"
            @update:modelValue="v => store.setAt(['environments', String(name), 'description'], v)" />

          <h4 class="block-title">prepare_env — 启动该 env 前的工作</h4>
          <ActionEditor
            :modelValue="(env as any).prepare_env"
            label="prepare_env"
            @update:modelValue="v => store.setAt(['environments', String(name), 'prepare_env'], v)"
          />

          <h4 class="block-title">clean_env — 跑完该 env 后的清理</h4>
          <ActionEditor
            :modelValue="(env as any).clean_env"
            label="clean_env"
            @update:modelValue="v => store.setAt(['environments', String(name), 'clean_env'], v)"
          />

          <h4 class="block-title">roles — 运行时角色 (host × instance × actions)</h4>
          <div class="roles-list">
            <details
              v-for="(role, rname) in (env as any).roles"
              :key="String(rname)"
              class="role-card"
              open
            >
              <summary class="role-summary">
                <span class="r-name">{{ String(rname) }}</span>
                <span class="r-meta">{{ instanceCount((role as any).instances) }} instance(s)</span>
                <button class="btn-icon" @click.stop="removeRole(String(name), String(rname))">×</button>
              </summary>
              <div class="role-form">
                <FormField name="host" label="role 默认 host" :modelValue="(role as any).host"
                  :schema="hostSchema"
                  @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'host'], v)" />

                <h5 class="sub-title">instances</h5>
                <div class="instance-list">
                  <div v-for="(inst, idx) in (role as any).instances" :key="idx" class="instance-card">
                    <div class="instance-header">
                      <span class="inst-name">{{ (inst as any).name }}</span>
                      <button class="btn-icon" @click="removeInstance(String(name), String(rname), idx)">×</button>
                    </div>
                    <div class="instance-fields">
                      <FormField :name="`name-${idx}`" label="name"
                        :modelValue="(inst as any).name"
                        :schema="nameSchema" :required="true"
                        @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'instances', idx, 'name'], v)" />
                      <FormField :name="`host-${idx}`" label="host"
                        :modelValue="(inst as any).host"
                        :schema="hostSchema" :required="true"
                        @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'instances', idx, 'host'], v)" />
                      <FormField :name="`port-${idx}`" label="port"
                        :modelValue="(inst as any).port"
                        :schema="portSchema"
                        @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'instances', idx, 'port'], v)" />
                      <FormField :name="`libvirt-${idx}`" label="libvirt_uri"
                        :modelValue="(inst as any).libvirt_uri"
                        :schema="strSchema"
                        @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'instances', idx, 'libvirt_uri'], v)" />
                      <FormField :name="`desc-${idx}`" label="description"
                        :modelValue="(inst as any).description"
                        :schema="descSchema"
                        @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'instances', idx, 'description'], v)" />
                    </div>
                  </div>
                </div>
                <button class="btn-sm" @click="addInstance(String(name), String(rname))">+ 新增 instance</button>

                <h5 class="sub-title">actions (per-role lifecycle)</h5>
                <div class="action-list">
                  <div v-for="(act, aname) in (role as any).actions" :key="String(aname)" class="action-card">
                    <div class="action-header">
                      <span class="a-name">{{ String(aname) }}</span>
                      <button class="btn-icon" @click="removeAction(String(name), String(rname), String(aname))">×</button>
                    </div>
                    <ActionEditor
                      :modelValue="act"
                      :label="String(aname)"
                      @update:modelValue="v => store.setAt(['environments', String(name), 'roles', String(rname), 'actions', String(aname)], v)"
                    />
                  </div>
                </div>
                <div class="add-action-row">
                  <input v-model="newActionName[name + '/' + rname]" class="action-input"
                    placeholder="action 名 (start/stop/restart/deploy)" />
                  <button class="btn-sm"
                    :disabled="!newActionName[name + '/' + rname]"
                    @click="addAction(String(name), String(rname))">+ 新增 action</button>
                </div>
              </div>
            </details>
          </div>
          <button class="btn-sm" @click="addRole(String(name))">+ 新增 role</button>

          <h4 class="block-title">actions (cross-role orchestration)</h4>
          <div class="action-list">
            <div v-for="(act, aname) in (env as any).actions" :key="String(aname)" class="action-card">
              <div class="action-header">
                <span class="a-name">{{ String(aname) }}</span>
                <button class="btn-icon" @click="removeCrossAction(String(name), String(aname))">×</button>
              </div>
              <ActionEditor
                :modelValue="act"
                :label="String(aname)"
                @update:modelValue="v => store.setAt(['environments', String(name), 'actions', String(aname)], v)"
              />
            </div>
          </div>
          <div class="add-action-row">
            <input v-model="newCrossActionName[name]" class="action-input"
              placeholder="cross action 名 (health/verify/setup)" />
            <button class="btn-sm" :disabled="!newCrossActionName[name]"
              @click="addCrossAction(String(name))">+ 新增 cross action</button>
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import FormField from '@/components/fields/FormField.vue'
import ActionEditor from '@/components/fields/ActionEditor.vue'
import type { JSONSchema7 } from '@/types/schema'
import { s } from '@/utils/coerce'

const store = useProjectStore()
const environments = computed<Record<string, unknown>>(() => store.getSection('environments'))

const strSchema: JSONSchema7 = { type: 'string' }
const descSchema: JSONSchema7 = { type: 'string', multiline: true, description: '文本描述' }
const hostSchema: JSONSchema7 = {
  type: 'string', minLength: 1,
  description: '物理主机标识. 支持模板 {instance.host}',
}
const nameSchema: JSONSchema7 = {
  type: 'string', pattern: '^[a-z][a-z0-9-]*$', minLength: 1,
  description: '命名约定: source-agent / target-agent / backend-1 / agent-a',
}
const portSchema: JSONSchema7 = {
  type: 'integer', minimum: 1, maximum: 65535,
  description: '端口. 同 host 内同 role 必须唯一',
}

const newActionName = reactive<Record<string, string>>({})
const newCrossActionName = reactive<Record<string, string>>({})

function rolesList(env: any): string {
  if (!env?.roles) return ''
  return Object.keys(env.roles).join(', ')
}

function instanceCount(arr: unknown[] | undefined): number {
  return arr?.length || 0
}

function onToggle(e: Event, name: string) {
  if ((e.target as HTMLDetailsElement).open) {
    store.selectEnv(name)
  }
}

function addEnvironment() {
  const name = prompt('environment 名称 (如: dev-local):')
  if (!name) return
  store.setAt(['environments', name], {
    roles: {
      backend: { instances: [{ name: 'backend-1', host: 'localhost' }] },
    },
  })
  store.selectEnv(name)
}

function removeEnvironment(name: string) {
  if (!confirm(`删除 environment "${name}"?`)) return
  store.deleteAt(['environments', name])
}

function addRole(envName: string) {
  const rname = prompt('role 名称 (如: backend/frontend/agent):')
  if (!rname) return
  store.setAt(['environments', envName, 'roles', rname], {
    instances: [{ name: `${rname}-1`, host: 'localhost' }],
  })
}

function removeRole(envName: string, rname: string) {
  if (!confirm(`删除 role "${rname}"?`)) return
  store.deleteAt(['environments', envName, 'roles', rname])
}

function addInstance(envName: string, rname: string) {
  const cur = store.getAt(['environments', envName, 'roles', rname, 'instances']) as unknown[] || []
  store.setAt(['environments', envName, 'roles', rname, 'instances'], [
    ...cur,
    { name: `${rname}-${cur.length + 1}`, host: 'localhost' },
  ])
}

function removeInstance(envName: string, rname: string, idx: number) {
  if (!confirm('删除 instance?')) return
  store.deleteAt(['environments', envName, 'roles', rname, 'instances', idx])
}

function addAction(envName: string, rname: string) {
  const key = `${envName}/${rname}`
  const aname = (newActionName[key] || '').trim()
  if (!aname) return
  store.setAt(['environments', envName, 'roles', rname, 'actions', aname], {
    host: 'localhost',
    script: '',
  })
  newActionName[key] = ''
}

function removeAction(envName: string, rname: string, aname: string) {
  if (!confirm(`删除 action "${aname}"?`)) return
  store.deleteAt(['environments', envName, 'roles', rname, 'actions', aname])
}

function addCrossAction(envName: string) {
  const aname = (newCrossActionName[envName] || '').trim()
  if (!aname) return
  store.setAt(['environments', envName, 'actions', aname], {
    host: 'localhost',
    script: '',
  })
  newCrossActionName[envName] = ''
}

function removeCrossAction(envName: string, aname: string) {
  if (!confirm(`删除 cross action "${aname}"?`)) return
  store.deleteAt(['environments', envName, 'actions', aname])
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
.env-list { display: flex; flex-direction: column; gap: 8px; }
.env-card {
  background: #2d2d2d; border: 1px solid #3c3c3c; border-radius: 6px;
  overflow: hidden;
}
.env-card.active { border-color: #4fc3f7; }
.env-summary {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; cursor: pointer; user-select: none;
}
.env-summary:hover { background: #333; }
.env-card[open] .env-summary { background: #2a2a2a; border-bottom: 1px solid #3c3c3c; }
.e-name { font-weight: 600; color: #e0e0e0; min-width: 120px; }
.e-meta { color: #888; font-size: 12px; flex: 1; }
.btn-icon {
  background: transparent; border: none; color: #aaa;
  width: 22px; height: 22px; font-size: 14px; border-radius: 3px;
}
.btn-icon:hover { background: #b71c1c; color: #fff; }
.env-form { padding: 16px; display: flex; flex-direction: column; gap: 4px; }
.block-title {
  margin: 16px 0 8px; font-size: 13px; color: #4fc3f7;
  border-bottom: 1px dashed #3c3c3c; padding-bottom: 4px;
}
.roles-list { display: flex; flex-direction: column; gap: 8px; }
.role-card {
  background: #252526; border: 1px solid #3c3c3c; border-radius: 4px;
  margin-bottom: 6px;
}
.role-summary {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; cursor: pointer; user-select: none;
}
.role-summary:hover { background: #2a2a2a; }
.r-name { font-weight: 600; color: #e0e0e0; min-width: 100px; }
.r-meta { color: #888; font-size: 11px; flex: 1; }
.role-form { padding: 12px; display: flex; flex-direction: column; gap: 4px; }
.sub-title {
  margin: 12px 0 6px; font-size: 11px; color: #aaa;
  font-family: Consolas, monospace; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
}
.instance-list { display: flex; flex-direction: column; gap: 6px; }
.instance-card {
  background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px;
  padding: 10px;
}
.instance-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px;
}
.inst-name { color: #4fc3f7; font-family: Consolas, monospace; font-size: 12px; font-weight: 600; }
.instance-fields { display: flex; flex-direction: column; gap: 4px; }
.action-list { display: flex; flex-direction: column; gap: 8px; }
.action-card {
  background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px;
  padding: 10px;
}
.action-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.a-name { color: #ffb74d; font-family: Consolas, monospace; font-size: 12px; font-weight: 600; }
.add-action-row { display: flex; gap: 6px; margin-top: 8px; }
.action-input {
  flex: 1; background: #1e1e1e; border: 1px solid #3c3c3c; color: #d4d4d4;
  padding: 4px 8px; border-radius: 4px; font-size: 12px;
  font-family: Consolas, monospace;
}
.action-input:focus { outline: none; border-color: #4fc3f7; }
</style>