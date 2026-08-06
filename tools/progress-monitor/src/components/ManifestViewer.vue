<script setup lang="ts">
import { computed, ref } from 'vue'
import { usePipelineStore } from '@/stores/pipelineStore'

const store = usePipelineStore()
const mode = ref<'structured' | 'raw'>('structured')
const yamlText = computed(() => {
  if (store.manifestRaw) return store.manifestRaw
  return store.manifest ? JSON.stringify(store.manifest, null, 2) : '# no manifest data'
})
</script>

<template>
  <div class="manifest-viewer">
    <div class="toolbar">
      <strong>execution-manifest.yaml</strong>
      <button :class="{ active: mode === 'structured' }" @click="mode = 'structured'">结构化</button>
      <button :class="{ active: mode === 'raw' }" @click="mode = 'raw'">原始 YAML</button>
    </div>
    <div v-if="mode === 'structured'" class="structured">
      <section v-for="stage in store.manifest?.stages || []" :key="stage.name">
        <h3>{{ stage.name }} <small>{{ stage.environment }}</small></h3>
        <table>
          <thead><tr><th>Track</th><th>类型</th><th>启用</th><th>原因</th><th>阶段</th></tr></thead>
          <tbody>
            <tr v-for="track in stage.tracks" :key="track.id">
              <td>{{ track.id }}</td><td>{{ track.type }}</td><td>{{ track.enabled ? '是' : '否' }}</td>
              <td>{{ track.reason || '-' }}</td><td>{{ Object.keys(track.phase_prompts || {}).join(' → ') || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
      <section v-if="store.manifest?.final_gate" class="final-gate-summary">
        <h3>final-gate</h3>
        <p>在所有 Track 执行完成后，汇总检查各 Track 的 Gate 结果；通过后，流水线才会完成并归档。</p>
      </section>
    </div>
    <pre v-else class="yaml-text">{{ yamlText }}</pre>
  </div>
</template>

<style scoped>
.manifest-viewer { height: 100%; display: flex; flex-direction: column; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 16px; border-bottom: 1px solid var(--border-color); }
.toolbar strong { margin-right: auto; }
.toolbar button { padding: 5px 10px; background: white; border: 1px solid var(--border-color); border-radius: 4px; cursor: pointer; }
.toolbar button.active { color: var(--color-running); border-color: var(--color-running); }
.structured, .yaml-text { flex: 1; overflow: auto; padding: 16px; }
section { margin-bottom: 22px; }
.final-gate-summary { padding-top: 2px; }
.final-gate-summary p { margin: 0; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
h3 { margin-bottom: 8px; }
h3 small { margin-left: 8px; color: var(--text-muted); font-weight: normal; }
table { width: 100%; border-collapse: collapse; background: white; }
th, td { text-align: left; padding: 8px 10px; border: 1px solid var(--border-color); font-size: 13px; }
th { color: var(--text-muted); }
.yaml-text { white-space: pre-wrap; font-family: var(--font-mono); font-size: 13px; line-height: 1.5; margin: 0; }
</style>
