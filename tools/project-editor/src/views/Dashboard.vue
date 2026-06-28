<template>
  <div class="dashboard">
    <header class="dashboard-header">
      <h1>📊 项目仪表板</h1>
      <p class="subtitle">{{ summary }}</p>
    </header>

    <section class="health-section">
      <h2>健康度</h2>
      <div class="health-grid">
        <div class="health-card" :class="cardClass('modules')">
          <div class="hc-label">modules</div>
          <div class="hc-value">{{ Object.keys(store.data.modules || {}).length }}</div>
        </div>
        <div class="health-card" :class="cardClass('environments')">
          <div class="hc-label">environments</div>
          <div class="hc-value">{{ Object.keys(store.data.environments || {}).length }}</div>
        </div>
        <div class="health-card" :class="cardClass('tracks')">
          <div class="hc-label">tracks</div>
          <div class="hc-value">{{ Object.keys(store.data.tracks || {}).length }}</div>
        </div>
        <div class="health-card" :class="cardClass('stages')">
          <div class="hc-label">stages</div>
          <div class="hc-value">{{ stagesLen }}</div>
        </div>
        <div class="health-card" :class="cardClass('regression')">
          <div class="hc-label">regression suites</div>
          <div class="hc-value">{{ regressionLen }}</div>
        </div>
        <div class="health-card" :class="cardClass('schema')">
          <div class="hc-label">schema</div>
          <div class="hc-value hc-ok">{{ store.isValid ? '✓' : '✗' }}</div>
        </div>
      </div>
    </section>

    <section class="modules-section">
      <h2>模块构成</h2>
      <div class="mod-list">
        <div
          v-for="(mod, name) in store.data.modules"
          :key="name as string"
          class="mod-pill"
          :class="`lang-${(mod as any).language}`"
          @click="goToForm(name as string)"
        >
          <span class="pill-name">{{ name as string }}</span>
          <span class="pill-lang">{{ (mod as any).language }}</span>
        </div>
      </div>
    </section>

    <section class="pipeline-section">
      <h2>Pipeline 流水线</h2>
      <div class="pipeline">
        <div
          v-for="(stage, idx) in stages"
          :key="idx"
          class="pipe-stage"
        >
          <div class="pipe-name">{{ (stage as any).name }}</div>
          <div class="pipe-tracks">
            <span
              v-for="t in (stage as any).tracks"
              :key="t"
              class="pipe-track"
            >{{ t }}</span>
          </div>
          <div v-if="idx < stages.length - 1" class="pipe-arrow">↓</div>
        </div>
      </div>
    </section>

    <section class="quick-actions">
      <h2>快速操作</h2>
      <div class="qa-grid">
        <button class="qa-btn" @click="store.setView('form'); store.selectModule('')">
          📋 进表单编辑
        </button>
        <button class="qa-btn" @click="store.setView('canvas')">
          📐 看画布关系图
        </button>
        <button class="qa-btn" @click="reload">
          ↻ 重新加载 project.yaml
        </button>
      </div>
    </section>

    <section v-if="store.errors.length > 0" class="errors-section">
      <h2>⚠ 当前 schema 错误</h2>
      <ul class="errors-list">
        <li v-for="(e, i) in store.errors.slice(0, 5)" :key="i" class="error-item">
          <code>{{ e.path || '/' }}</code> — {{ e.message }}
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useProjectStore } from '@/stores/projectStore'

const store = useProjectStore()

const stages = computed(() => (store.data.stages as unknown[]) || [])
const stagesLen = computed(() => stages.value.length)
const regressionLen = computed(() => Object.keys(((store.data.regression as any)?.suite) || {}).length)

const summary = computed(() => {
  const total = Object.keys(store.data.modules || {}).length
    + Object.keys(store.data.environments || {}).length
    + Object.keys(store.data.tracks || {}).length
  return `本项目共 ${total} 个核心段元素, ${stagesLen.value} 个 pipeline 阶段`
})

function cardClass(key: string): string {
  if (key === 'schema') return store.isValid ? 'ok' : 'err'
  const v = key === 'stages' ? stagesLen.value
    : key === 'regression' ? regressionLen.value
    : Object.keys((store.data as any)[key] || {}).length
  return v > 0 ? 'ok' : 'warn'
}

function goToForm(moduleName: string) {
  store.setView('form')
  store.selectModule(moduleName)
}

function reload() {
  store.reload()
}
</script>

<style scoped>
.dashboard {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 24px;
  overflow-y: auto;
  height: 100%;
}
.dashboard-header h1 {
  margin: 0; font-size: 22px; color: #e0e0e0; font-weight: 600;
}
.subtitle { color: #888; font-size: 13px; margin: 4px 0 0; }
section h2 {
  font-size: 14px; color: #aaa; text-transform: uppercase;
  letter-spacing: 0.05em; margin: 0 0 12px;
}
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}
.health-card {
  background: #2d2d2d;
  border: 1px solid #3c3c3c;
  border-radius: 6px;
  padding: 16px;
  border-left: 4px solid #555;
}
.health-card.ok { border-left-color: #66bb6a; }
.health-card.warn { border-left-color: #ffa726; }
.health-card.err { border-left-color: #ef5350; }
.hc-label { font-size: 11px; color: #888; text-transform: uppercase; }
.hc-value { font-size: 28px; font-weight: 700; color: #e0e0e0; margin-top: 4px; }
.hc-ok { color: #81c784; }

.mod-list { display: flex; flex-wrap: wrap; gap: 8px; }
.mod-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 16px;
  background: #2d2d2d; border: 1px solid #3c3c3c;
  cursor: pointer; font-size: 12px;
  transition: all .15s;
}
.mod-pill:hover { background: #333; border-color: #4fc3f7; }
.pill-name { font-weight: 600; color: #e0e0e0; }
.pill-lang {
  background: #455a64; color: #cfd8dc; padding: 2px 8px;
  border-radius: 10px; font-size: 10px; font-family: Consolas, monospace;
}
.lang-java { border-left: 3px solid #ff7043; }
.lang-go { border-left: 3px solid #29b6f6; }
.lang-typescript { border-left: 3px solid #7e57c2; }
.lang-proto { border-left: 3px solid #66bb6a; }
.lang-shell { border-left: 3px solid #ffa726; }
.lang-python { border-left: 3px solid #ffd54f; }

.pipeline {
  display: flex; flex-direction: column; gap: 4px;
  background: #2d2d2d; padding: 16px; border-radius: 6px;
}
.pipe-stage {
  display: flex; align-items: center; gap: 12px;
}
.pipe-name {
  font-weight: 600; color: #4fc3f7; min-width: 140px;
  font-family: Consolas, monospace; font-size: 13px;
}
.pipe-tracks { display: flex; gap: 6px; flex: 1; flex-wrap: wrap; }
.pipe-track {
  background: #1e1e1e; padding: 2px 10px; border-radius: 3px;
  font-family: Consolas, monospace; font-size: 11px;
  color: #ffb74d; border: 1px solid #3c3c3c;
}
.pipe-arrow { color: #555; font-size: 16px; padding-left: 8px; }

.qa-grid { display: flex; gap: 12px; flex-wrap: wrap; }
.qa-btn {
  background: #2d2d2d; border: 1px solid #3c3c3c; color: #d4d4d4;
  padding: 12px 20px; border-radius: 6px; font-size: 13px;
  cursor: pointer;
}
.qa-btn:hover { background: #333; border-color: #4fc3f7; }

.errors-section { background: #2d2d2d; padding: 12px 16px; border-radius: 6px; border-left: 4px solid #ef5350; }
.errors-list { margin: 0; padding-left: 16px; }
.error-item { color: #ef5350; font-size: 12px; margin-bottom: 4px; }
.error-item code {
  background: #1e1e1e; padding: 1px 4px; border-radius: 2px;
  font-family: Consolas, monospace; color: #ffb74d;
}
</style>