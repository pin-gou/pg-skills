<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { usePipelineStore } from '@/stores/pipelineStore'
import { STATUS_ICONS, STATUS_COLORS, PHASE_ICONS } from '@/types/pipeline'
import type { TreeNode, PhaseState, TrackState } from '@/types/pipeline'
import { buildAutoExpandSet, findNodeById } from '@/composables/buildTree'
import { api } from '@/api/client'
import { formatPipelineTimestamp } from '@/shared/dateTime'

const store = usePipelineStore()

const expanded = ref<Set<string>>(new Set())
const selectedId = ref<string | null>(null)
const previewContent = ref<string | null>(null)
const previewTitle = ref<string>('')
const previewMode = ref<'summary' | 'artifact'>('summary')
const artifacts = ref<string[]>([])
const loadingArtifact = ref(false)
const followCurrent = ref(true)
const initializedExpand = ref(false)

const tree = computed(() => store.tree)

function initExpand() {
  if (tree.value.length > 0) {
    const automatic = buildAutoExpandSet(tree.value)
    if (!initializedExpand.value) {
      expanded.value = automatic
      initializedExpand.value = true
    } else if (followCurrent.value) {
      expanded.value = new Set([...expanded.value, ...automatic])
    }
  }
}

watch(tree, initExpand, { immediate: true })

function toggleExpand(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expanded.value = next
}

function getStatusIcon(status: string): string {
  return STATUS_ICONS[status] || '○'
}

function getStatusColor(status: string): string {
  return STATUS_COLORS[status] || STATUS_COLORS.pending
}

function getPhaseIcon(phase: string): string {
  return PHASE_ICONS[phase] || '📄'
}

function getStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '等待', in_progress: '进行中', running: '运行中',
    completed: '完成', pass: '通过', failed: '失败', fail: '失败',
    escalate: '升级', skipped: '跳过',
  }
  return map[status] || status
}

function selectNode(node: TreeNode) {
  selectedId.value = node.id
  previewMode.value = 'summary'
  previewContent.value = null
  artifacts.value = []

  if (node.type === 'phase' && node.meta) {
    const phaseState = node.meta.phaseState as PhaseState | undefined
    const track = node.meta.track as string
    const phase = node.meta.phase as string

    if (phaseState) {
      previewTitle.value = `${track}:${phase}`
      previewContent.value = [
        `状态: ${getStatusLabel(phaseState.status)}`,
        `Agent: ${phaseState.agent || '-'}`,
        `尝试次数: ${phaseState.attempt ?? 0}`,
        `开始时间: ${formatPipelineTimestamp(phaseState.started_at)}`,
        `完成时间: ${formatPipelineTimestamp(phaseState.completed_at)}`,
        `摘要: ${phaseState.summary || '-'}`,
        `报告: ${phaseState.report_path || '-'}`,
      ].join('\n')
      loadArtifacts(track, phase)
    } else {
      previewTitle.value = `${track}:${phase}`
      previewContent.value = '暂无状态信息'
    }
  } else if (node.type === 'fix-cycle' && node.meta) {
    const pt = node.meta.parentTrack as string
    const pp = node.meta.parentPhase as string
    const cycle = node.meta.cycle as number
    previewTitle.value = `${pt}:${pp} - fix cycle ${cycle}`
    previewContent.value = `修复循环 #${cycle}\n状态: ${getStatusLabel(node.status)}`
  } else if (node.type === 'final-gate' && node.meta) {
    const phaseState = node.meta.phaseState as PhaseState | undefined
    previewTitle.value = 'final-gate'
    previewContent.value = [
      `状态: ${getStatusLabel(phaseState?.status || node.status)}`,
      `Agent: ${phaseState?.agent || '-'}`,
      `尝试次数: ${phaseState?.attempt ?? 0}`,
      `开始时间: ${formatPipelineTimestamp(phaseState?.started_at)}`,
      `完成时间: ${formatPipelineTimestamp(phaseState?.completed_at)}`,
      `摘要: ${phaseState?.summary || '-'}`,
      `报告: ${phaseState?.report_path || '-'}`,
    ].join('\n')
    loadArtifacts('final-gate', 'gate')
  } else if (node.type === 'track' && node.meta) {
    const ts = node.meta.trackState as TrackState | undefined
    previewTitle.value = node.label
    if (ts) {
      const allPhases = Object.entries(ts.phases)
      const lines = allPhases.map(([p, ps]) =>
        `  ${getStatusIcon(ps.status)} ${p}: ${getStatusLabel(ps.status)}${ps.summary ? ' — ' + ps.summary.slice(0, 60) : ''}`
      )
      previewContent.value = `Track: ${node.label}\n状态: ${getStatusLabel(ts.status)}\n\n${lines.join('\n')}`
    } else {
      previewContent.value = '暂无状态信息'
    }
  } else if (node.type === 'stage') {
    previewTitle.value = node.label
    const env = node.meta?.environment as string || ''
    previewContent.value = `Stage: ${node.label}\n\n环境: ${env}\nTracks: ${node.children.length}`
  } else {
    previewTitle.value = node.label
    previewContent.value = '选择节点查看详情'
  }
}

async function loadArtifacts(track: string, phase: string) {
  try {
    artifacts.value = await api.listArtifacts(store.currentChange!, track, phase)
  } catch {
    artifacts.value = []
  }
}

async function viewArtifact(filePath: string) {
  loadingArtifact.value = true
  try {
    const content = await api.getArtifactContent(store.currentChange!, filePath)
    previewMode.value = 'artifact'
    if (filePath.toLowerCase().endsWith('.json')) {
      try {
        previewContent.value = JSON.stringify(JSON.parse(content), null, 2)
      } catch {
        previewContent.value = content
      }
    } else {
      previewContent.value = content
    }
    previewTitle.value = filePath
  } catch (e) {
    previewContent.value = `加载失败: ${e}`
  } finally {
    loadingArtifact.value = false
  }
}

function showSummary() {
  previewMode.value = 'summary'
  const node = findNodeById(tree.value, selectedId.value || '')
  if (node) selectNode(node)
}

watch(() => store.refreshVersion, () => {
  const node = selectedId.value ? findNodeById(tree.value, selectedId.value) : null
  if ((node?.type === 'phase' || node?.type === 'final-gate') && node.meta) {
    loadArtifacts(node.meta.track as string, node.meta.phase as string)
  }
})
</script>

<template>
  <div class="progress-container">
    <div class="tree-panel">
      <label class="follow-toggle">
        <input v-model="followCurrent" type="checkbox" /> 自动展开当前步骤
      </label>
      <div v-for="stage in tree" :key="stage.id" class="tree-section">
        <div
          :class="['tree-node', 'stage-node', { selected: selectedId === stage.id }]"
          @click="selectNode(stage)"
        >
          <span class="expand-icon" @click.stop="toggleExpand(stage.id)">
            {{ expanded.has(stage.id) ? '▼' : '▶' }}
          </span>
          <span class="node-icon">📁</span>
          <span class="node-label">{{ stage.label }}</span>
        </div>

        <div v-if="expanded.has(stage.id)" class="tree-children">
          <div v-for="track in stage.children" :key="track.id" class="tree-section">
            <div
              :class="['tree-node', 'track-node', { selected: selectedId === track.id }]"
              @click="selectNode(track)"
            >
              <span v-if="track.children.length > 0" class="expand-icon" @click.stop="toggleExpand(track.id)">
                {{ expanded.has(track.id) ? '▼' : '▶' }}
              </span>
              <span v-else class="expand-icon placeholder"></span>
              <span class="node-icon">{{ track.meta?.type === 'simple' ? '⚡' : '🔧' }}</span>
              <span class="node-label">{{ track.label }}</span>
              <span class="node-status" :style="{ color: getStatusColor(track.status) }">
                {{ getStatusIcon(track.status) }}
              </span>
            </div>

            <div v-if="expanded.has(track.id)" class="tree-children">
              <div v-for="phase in track.children" :key="phase.id">
                <div
                  :class="['tree-node', 'phase-node', { selected: selectedId === phase.id, in_progress: phase.status === 'in_progress' || phase.status === 'running' }]"
                  @click="selectNode(phase)"
                >
                  <span class="expand-icon" v-if="phase.children.length > 0" @click.stop="toggleExpand(phase.id)">
                    {{ expanded.has(phase.id) ? '▼' : '▶' }}
                  </span>
                  <span class="expand-icon placeholder" v-else></span>
                  <span class="node-icon">{{ getPhaseIcon(phase.label) }}</span>
                  <span class="node-label">{{ phase.label }}</span>
                  <span class="node-status" :style="{ color: getStatusColor(phase.status) }">
                    {{ getStatusIcon(phase.status) }}
                  </span>
                </div>

                <div v-if="phase.children.length > 0 && expanded.has(phase.id)" class="tree-children">
                  <div v-for="fc in phase.children" :key="fc.id">
                    <div
                      :class="['tree-node', 'fix-cycle-node', { selected: selectedId === fc.id }]"
                      @click="selectNode(fc)"
                    >
                      <span class="expand-icon" v-if="fc.children.length > 0" @click.stop="toggleExpand(fc.id)">
                        {{ expanded.has(fc.id) ? '▼' : '▶' }}
                      </span>
                      <span class="expand-icon placeholder" v-else></span>
                      <span class="node-icon">🔄</span>
                      <span class="node-label">{{ fc.label }}</span>
                      <span class="node-status" :style="{ color: getStatusColor(fc.status) }">
                        {{ getStatusIcon(fc.status) }}
                      </span>
                    </div>

                    <div v-if="fc.children.length > 0 && expanded.has(fc.id)" class="tree-children">
                      <div v-for="sub in fc.children" :key="sub.id">
                        <div
                          :class="['tree-node', 'sub-phase-node', { selected: selectedId === sub.id }]"
                          @click="selectNode(sub)"
                        >
                          <span class="expand-icon placeholder"></span>
                          <span class="node-icon">{{ sub.label.startsWith('fix') ? '🔨' : '✅' }}</span>
                          <span class="node-label">{{ sub.label }}</span>
                          <span class="node-status" :style="{ color: getStatusColor(sub.status) }">
                            {{ getStatusIcon(sub.status) }}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="preview-panel">
      <div class="preview-header">
        <span class="preview-title">{{ previewTitle }}</span>
        <button v-if="previewMode === 'artifact'" class="back-to-summary" @click="showSummary">← 返回摘要</button>
      </div>

      <div v-if="previewMode === 'summary'" class="preview-body">
        <div v-if="selectedId" class="summary-content">
          <pre class="summary-text">{{ previewContent }}</pre>

          <div v-if="artifacts.length > 0" class="artifact-list">
            <div class="artifact-label">关联产物:</div>
            <button
              v-for="art in artifacts"
              :key="art"
              class="artifact-btn"
              @click="viewArtifact(art)"
            >
              📄 {{ art }}
            </button>
          </div>
        </div>
        <div v-else class="preview-empty">点击左侧树节点查看详情</div>
      </div>

      <div v-else class="preview-body artifact-content">
        <div v-if="loadingArtifact" class="loading">加载中...</div>
        <pre v-else class="artifact-text">{{ previewContent }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.progress-container { display: flex; height: 100%; }

.tree-panel {
  width: 40%; min-width: 320px; overflow: auto;
  border-right: 1px solid var(--border-color);
  background: var(--bg-card); padding: 8px 0;
}
.follow-toggle { display: block; padding: 6px 12px 10px; color: var(--text-muted); font-size: 12px; border-bottom: 1px solid var(--border-color); }

.tree-section { user-select: none; }

.tree-node {
  display: flex; align-items: center; gap: 4px;
  padding: 6px 12px; cursor: pointer;
  transition: background 0.1s; font-size: 13px;
  border-left: 3px solid transparent;
}
.tree-node:hover { background: #f0f5ff; }
.tree-node.selected { background: #ecf5ff; border-left-color: var(--color-running); }
.tree-node.in_progress { background: #f0f9ff; }

.stage-node { font-weight: 600; font-size: 14px; padding: 8px 12px; }
.track-node { padding-left: 28px; }
.phase-node { padding-left: 48px; }
.fix-cycle-node { padding-left: 68px; font-size: 12px; }
.sub-phase-node { padding-left: 88px; font-size: 12px; }

.expand-icon { width: 16px; text-align: center; font-size: 10px; color: var(--text-muted); flex-shrink: 0; }
.expand-icon.placeholder { visibility: hidden; }
.node-icon { width: 20px; text-align: center; flex-shrink: 0; }
.node-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node-status { font-weight: 600; font-size: 14px; flex-shrink: 0; }

.preview-panel { flex: 1; display: flex; flex-direction: column; background: var(--bg-card); }
.preview-header {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px; border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.preview-title { font-weight: 600; font-size: 14px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.back-to-summary {
  background: none; border: 1px solid var(--border-color);
  border-radius: 4px; padding: 4px 10px; cursor: pointer;
  font-size: 12px; color: var(--text-secondary); flex-shrink: 0;
}
.back-to-summary:hover { border-color: var(--color-running); color: var(--color-running); }

.preview-body { flex: 1; overflow: auto; padding: 16px; }
.preview-empty { color: var(--text-muted); text-align: center; padding: 40px; }
.summary-text { white-space: pre-wrap; font-family: var(--font-mono); font-size: 13px; line-height: 1.6; color: var(--text-primary); }

.artifact-list { margin-top: 20px; border-top: 1px solid var(--border-color); padding-top: 12px; }
.artifact-label { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.artifact-btn {
  display: block; width: 100%; text-align: left;
  background: none; border: 1px solid var(--border-color);
  border-radius: 4px; padding: 8px 12px; margin-bottom: 4px;
  cursor: pointer; font-size: 13px; font-family: var(--font-mono);
  color: var(--text-secondary); transition: all 0.1s;
}
.artifact-btn:hover { background: #f0f5ff; border-color: var(--color-running); color: var(--color-running); }

.artifact-content { padding: 0; }
.artifact-text { white-space: pre-wrap; font-family: var(--font-mono); font-size: 13px; line-height: 1.5; padding: 16px; margin: 0; }
.loading { padding: 40px; text-align: center; color: var(--text-muted); }
</style>
