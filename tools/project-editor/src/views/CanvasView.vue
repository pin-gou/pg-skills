<template>
  <div class="canvas-view">
    <header class="cv-header">
      <h2>📐 画布 — Stage → Track → Module 关系 (只读)</h2>
      <p class="cv-hint">蓝色虚线: 表单当前选中. 点击节点跳到对应表单段.</p>
    </header>

    <div class="canvas-grid">
      <!-- Column 1: Stages -->
      <div class="cv-col">
        <h3 class="col-title">Stages (顺序)</h3>
        <div
          v-for="(stage, idx) in stages"
          :key="idx"
          class="cv-node stage-node"
          :class="{
            active: store.selection.stage === (stage as any).name,
            highlight: highlightedStages.has((stage as any).name),
          }"
          @click="onStageClick((stage as any).name)"
        >
          <div class="node-idx">{{ idx + 1 }}</div>
          <div class="node-name">{{ (stage as any).name }}</div>
          <div class="node-meta">{{ (stage as any).tracks.length }} tracks</div>
        </div>
      </div>

      <!-- Column 2: Tracks -->
      <div class="cv-col">
        <h3 class="col-title">Tracks</h3>
        <div
          v-for="(track, name) in tracks"
          :key="name as string"
          class="cv-node track-node"
          :class="{
            active: store.selection.track === name,
            highlight: highlightedTracks.has(name),
            dim: !isTrackInCurrentStage(name as string),
          }"
          @click="onTrackClick(name as string)"
        >
          <div class="node-name">{{ name as string }}</div>
          <div class="node-meta">
            {{ (track as any).type || 'standard' }}
            · {{ ((track as any).modules || []).length }} modules
          </div>
        </div>
      </div>

      <!-- Column 3: Modules -->
      <div class="cv-col">
        <h3 class="col-title">Modules</h3>
        <div
          v-for="(mod, name) in modules"
          :key="name as string"
          class="cv-node module-node"
          :class="{
            active: store.selection.module === name,
            highlight: highlightedModules.has(name),
            dim: !isModuleReferenced(name as string),
          }"
          @click="onModuleClick(name as string)"
        >
          <div class="node-name">{{ name as string }}</div>
          <div class="node-meta">{{ (mod as any).language }} · {{ (mod as any).root }}</div>
        </div>
      </div>

      <!-- Column 4: Environments -->
      <div class="cv-col">
        <h3 class="col-title">Environments</h3>
        <div
          v-for="(env, name) in environments"
          :key="name as string"
          class="cv-node env-node"
          :class="{
            active: store.selection.env === name,
            highlight: highlightedEnvs.has(name),
          }"
          @click="onEnvClick(name as string)"
        >
          <div class="node-name">{{ name as string }}</div>
          <div class="node-meta">{{ Object.keys((env as any).roles || {}).join(', ') }}</div>
        </div>
      </div>
    </div>

    <div v-if="legendItems.length" class="legend">
      <h4>联动说明</h4>
      <div v-for="li in legendItems" :key="li.path" class="legend-item">
        <span class="legend-marker">▸</span>
        <span>{{ li.label }}: </span>
        <code>{{ li.path }}</code>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'

const store = useProjectStore()

const stages = computed(() => (store.data.stages as unknown[]) || [])
const tracks = computed<Record<string, unknown>>(() => store.getSection('tracks'))
const modules = computed<Record<string, unknown>>(() => store.getSection('modules'))
const environments = computed<Record<string, unknown>>(() => store.getSection('environments'))

const highlightedStages = computed(() => new Set<string>())
const highlightedTracks = computed(() => {
  const set = new Set<string>()
  const selStage = store.selection.stage
  if (selStage) {
    const stage = stages.value.find((s: any) => s.name === selStage) as any
    if (stage?.tracks) {
      for (const t of stage.tracks) set.add(t)
    }
  }
  if (store.selection.track) set.add(store.selection.track)
  return set
})
const highlightedModules = computed(() => {
  const set = new Set<string>()
  if (store.selection.module) set.add(store.selection.module)
  const selTrack = store.selection.track
  if (selTrack) {
    const track = tracks.value[selTrack] as any
    for (const m of (track?.modules || [])) set.add(m)
  }
  return set
})
const highlightedEnvs = computed(() => {
  const set = new Set<string>()
  if (store.selection.env) set.add(store.selection.env)
  return set
})

function isTrackInCurrentStage(trackName: string): boolean {
  const selStage = store.selection.stage
  if (!selStage) return true
  const stage = stages.value.find((s: any) => s.name === selStage) as any
  return stage?.tracks?.includes(trackName)
}

function isModuleReferenced(moduleName: string): boolean {
  const selTrack = store.selection.track
  if (!selTrack) return true
  const track = tracks.value[selTrack] as any
  return !track?.modules?.length || track.modules.includes(moduleName)
}

const legendItems = computed(() => {
  const items: Array<{ label: string; path: string }> = []
  if (store.selection.module) items.push({ label: 'Module', path: store.selection.module })
  if (store.selection.track) items.push({ label: 'Track', path: store.selection.track })
  if (store.selection.stage) items.push({ label: 'Stage', path: store.selection.stage })
  if (store.selection.env) items.push({ label: 'Env', path: store.selection.env })
  return items
})

function onStageClick(name: string) {
  store.selectStage(name)
  store.setView('form')
}
function onTrackClick(name: string) {
  store.selectTrack(name)
  store.setView('form')
}
function onModuleClick(name: string) {
  store.selectModule(name)
  store.setView('form')
}
function onEnvClick(name: string) {
  store.selectEnv(name)
  store.setView('form')
}
</script>

<style scoped>
.canvas-view {
  height: 100%;
  overflow: auto;
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cv-header h2 {
  margin: 0; font-size: 15px; color: #e0e0e0; font-weight: 600;
}
.cv-hint { color: #888; font-size: 12px; margin: 4px 0 0; }

.canvas-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  align-items: start;
}
.cv-col {
  background: #2d2d2d;
  border: 1px solid #3c3c3c;
  border-radius: 6px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.col-title {
  margin: 0 0 4px;
  font-size: 11px;
  text-transform: uppercase;
  color: #888;
  letter-spacing: 0.05em;
}
.cv-node {
  background: #1e1e1e;
  border: 1px solid #3c3c3c;
  border-radius: 4px;
  padding: 8px 10px;
  cursor: pointer;
  transition: all .15s;
}
.cv-node:hover { background: #252526; border-color: #555; }
.cv-node.dim { opacity: 0.4; }
.cv-node.active {
  border-color: #4fc3f7;
  background: #0d3a52;
}
.cv-node.highlight {
  border-color: #4fc3f7;
  box-shadow: 0 0 0 1px #4fc3f7;
}
.node-idx {
  display: inline-block;
  background: #455a64;
  color: #cfd8dc;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  margin-bottom: 4px;
}
.node-name {
  font-family: Consolas, monospace;
  font-weight: 600;
  color: #e0e0e0;
  font-size: 13px;
}
.node-meta { color: #888; font-size: 11px; margin-top: 2px; }

.stage-node .node-name { color: #4fc3f7; }
.track-node .node-name { color: #ffb74d; }
.module-node .node-name { color: #81c784; }
.env-node .node-name { color: #ce93d8; }

.legend {
  background: #252526;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  color: #aaa;
}
.legend h4 { margin: 0 0 8px; color: #d4d4d4; font-size: 12px; }
.legend-item { margin-bottom: 2px; }
.legend-marker { color: #4fc3f7; margin-right: 4px; }
.legend code {
  background: #1e1e1e;
  padding: 1px 4px;
  border-radius: 2px;
  font-family: Consolas, monospace;
}
</style>