import { isCompletedStatus } from '../shared/pipelineStatus.ts'

const FAILED = new Set(['failed', 'fail', 'workflow_failed', 'abandoned'])
const RUNNING = new Set(['in_progress', 'running'])
const SUCCESS = new Set(['completed', 'pass', 'skipped'])

export function aggregateStatuses(statuses: string[]): string {
  if (statuses.length === 0) return 'pending'
  if (statuses.some(status => FAILED.has(status))) return 'failed'
  if (statuses.some(status => status === 'escalate')) return 'escalate'
  if (statuses.some(status => RUNNING.has(status))) return 'in_progress'
  if (statuses.every(status => isCompletedStatus(status))) return 'completed'
  if (statuses.some(status => isCompletedStatus(status))) return 'in_progress'
  return 'pending'
}

export function finalGateStatus(snapshot: {
  status?: string
  current_track?: string
  current_phase?: string
  pipeline_order?: string[]
  tracks?: Record<string, { status?: string; phases?: Record<string, { status?: string }> }>
} | null): string {
  if (!snapshot) return 'pending'

  const finalTrack = snapshot.tracks?.['final-gate']
  const explicit = finalTrack?.phases?.gate?.status || finalTrack?.status
  if (explicit) return explicit

  const isCurrent = snapshot.current_track === 'final-gate' || snapshot.current_phase === 'final-gate'
  if (isCurrent) {
    return FAILED.has(snapshot.status || '') ? 'failed' : 'in_progress'
  }

  if (snapshot.pipeline_order?.includes('final-gate') && snapshot.status === 'completed') return 'pass'
  if (FAILED.has(snapshot.status || '')) return 'failed'
  return 'pending'
}

export function statusCategory(status: string): 'failed' | 'running' | 'completed' | 'pending' | 'escalate' {
  if (FAILED.has(status)) return 'failed'
  if (RUNNING.has(status)) return 'running'
  if (SUCCESS.has(status)) return 'completed'
  if (status === 'escalate') return 'escalate'
  return 'pending'
}
