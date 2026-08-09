export interface ChangeInfo {
  name: string
  isActive: boolean
  hasManifest: boolean
  hasSnapshot: boolean
  snapshotStatus: string | null
  currentStage: string | null
  currentTrack: string | null
  currentPhase: string | null
  failedReason: string | null
  completedPhases: number
  totalPhases: number
  lastEventAt: string | null
  isStalled: boolean
  mtime: string | null
  parseError: string | null
}

export interface Manifest {
  schema_version: string
  change: string
  stages: ManifestStage[]
  final_gate?: { tasks_md_section: string }
}

export interface ManifestStage {
  name: string
  environment: string
  tracks: ManifestTrack[]
}

export interface ManifestTrack {
  id: string
  type: string
  enabled: boolean
  reason: string
  phase_prompts?: Record<string, { tasks_md_section: string }>
  commands?: string[]
  scenario_yaml?: string
}

export interface Snapshot {
  schema_version: string
  change: string
  pipeline_order: string[]
  track_types: Record<string, string>
  tracks: Record<string, TrackState>
  status: string
  current_track: string
  current_phase: string
  stage_order: string[]
  stage_env_map: Record<string, string>
  current_stage: string
  stage_prepared: string[]
  current_sub_pipeline?: SubPipelineInfo
  failed_reason?: string
}

export interface SubPipelineInfo {
  kind: string
  parent_track: string
  parent_phase: string
  cycle: number
  phases: string[]
  current_index: number
  status?: string
  failed_reason?: string
  started_at?: string | null
  completed_at?: string | null
}

export interface TrackState {
  track_id: string
  bare: string
  label: string
  status: string
  started_at: string | null
  completed_at: string | null
  modules: string[]
  phases: Record<string, PhaseState>
  sub_pipelines: SubPipelineInfo[]
  code_review_enabled: boolean
  verify_enabled: boolean
  gate_enabled: boolean
  scenario_last_restart_attempt: number
}

export interface PhaseState {
  status: string
  attempt: number
  started_at: string | null
  completed_at: string | null
  agent: string | null
  report_path: string | null
  summary: string
  tasks_marked: number[]
  cycles: Record<string, unknown>[]
  fix_cycles: Record<string, unknown>[]
  review_fix_cycles: Record<string, unknown>[]
  gate_cycles: Record<string, unknown>[]
  current_cycle: number
}

export interface PipelineEvent {
  type: string
  timestamp?: string
  ts?: string
  track?: string
  phase?: string
  status?: string
  summary?: string
  _line?: number
  [key: string]: unknown
}

export interface EventsResponse {
  events: PipelineEvent[]
  total: number
}

export type TreeNodeType = 'stage' | 'track' | 'phase' | 'fix-cycle' | 'sub-phase' | 'final-gate' | 'cycle-step' | 'cycle-group'

export interface TreeNode {
  id: string
  label: string
  type: TreeNodeType
  status: string
  children: TreeNode[]
  meta?: Record<string, unknown>
  collapsed?: boolean
}

export const STATUS_ICONS: Record<string, string> = {
  pending: '○',
  in_progress: '●',
  running: '▶',
  completed: '✓',
  pass: '✓',
  failed: '✗',
  fail: '✗',
  escalate: '▲',
  skipped: '→',
}

export const STATUS_COLORS: Record<string, string> = {
  pending: '#909399',
  in_progress: '#409EFF',
  running: '#409EFF',
  completed: '#67C23A',
  pass: '#67C23A',
  failed: '#F56C6C',
  fail: '#F56C6C',
  escalate: '#E6A23C',
  skipped: '#C0C4CC',
}

export const PHASE_ICONS: Record<string, string> = {
  test: '🧪',
  dev: '🔧',
  review: '👁',
  verify: '✅',
  gate: '🏁',
  simple: '⚡',
  fix: '🔨',
  'fix-review': '🔨',
  'scenario-execute': '🎬',
  'scenario-fix': '🔨',
}

export const EVENT_TYPE_COLORS: Record<string, string> = {
  pipeline_started: '#409EFF',
  bootstrap_step_completed: '#67C23A',
  prepare_env_started: '#909399',
  prepare_env_completed: '#67C23A',
  clean_env_started: '#909399',
  clean_env_completed: '#67C23A',
  dispatch_started: '#409EFF',
  record_received: '#67C23A',
  fix_cycle_started: '#E6A23C',
  gate_cycle_started: '#E6A23C',
  sub_pipeline_completed: '#67C23A',
  track_completed: '#67C23A',
  pipeline_completed: '#67C23A',
  workflow_failed: '#F56C6C',
  git_commit: '#909399',
  dispatch_abandoned: '#F56C6C',
}
