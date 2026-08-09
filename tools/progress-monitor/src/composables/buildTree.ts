import type { Manifest, PhaseState, Snapshot, TreeNode, TrackState, SubPipelineInfo } from '../types/pipeline.ts'
import { aggregateStatuses, finalGateStatus } from './status.ts'

function phaseStatus(trackState: TrackState | undefined, phase: string): string {
  if (!trackState) return 'pending'
  const ps = trackState.phases[phase]
  if (!ps) return 'pending'
  return ps.status
}

function findTrackState(snapshot: Snapshot | null, qualifiedTrack: string): TrackState | undefined {
  if (!snapshot) return undefined
  return snapshot.tracks[qualifiedTrack]
}

function buildSubPipelineNodes(phases: SubPipelineInfo[]): TreeNode[] {
  const nodes: TreeNode[] = []
  for (const sp of phases) {
    const cycleLabel = `${sp.kind} cycle ${sp.cycle}`
    const children: TreeNode[] = []
    for (let i = 0; i < sp.phases.length; i++) {
      const subPhase = sp.phases[i]
      const subStatus = i < sp.current_index
        ? 'completed'
        : i === sp.current_index
          ? 'in_progress'
          : 'pending'
      children.push({
        id: `${sp.parent_track}.${sp.parent_phase}.${sp.kind}-${sp.cycle}.${subPhase}`,
        label: subPhase === 'fix' ? 'fix' : `${subPhase} (rerun)`,
        type: 'sub-phase',
        status: subStatus,
        children: [],
        meta: { spKind: sp.kind, cycle: sp.cycle, phase: subPhase, parentTrack: sp.parent_track },
      })
    }
    const inferredStatus = !sp.status
    nodes.push({
      id: `${sp.parent_track}.${sp.parent_phase}.${sp.kind}-${sp.cycle}`,
      label: cycleLabel,
      type: 'fix-cycle',
      status: sp.status || (children.every(c => c.status === 'completed') ? 'completed' : 'in_progress'),
      children,
      meta: {
        spKind: sp.kind,
        cycle: sp.cycle,
        parentTrack: sp.parent_track,
        parentPhase: sp.parent_phase,
        inferredStatus,
        failedReason: sp.failed_reason,
      },
    })
  }
  return nodes
}

function findSubPipelinesForPhase(trackState: TrackState | undefined, phase: string): SubPipelineInfo[] {
  if (!trackState) return []
  const result: SubPipelineInfo[] = []
  for (const sp of trackState.sub_pipelines) {
    if (sp.parent_phase === phase) {
      result.push(sp)
    }
  }
  return result
}

interface CycleRecord {
  kind: string
  cycle: number
  status: string
}

function readCycleList(list: unknown, kind: string): CycleRecord[] {
  if (!Array.isArray(list)) return []
  const result: CycleRecord[] = []
  for (let i = 0; i < list.length; i++) {
    const record = list[i] as Record<string, unknown> | null
    if (!record || typeof record !== 'object') continue
    result.push({
      kind,
      cycle: typeof record.cycle === 'number' ? record.cycle : i + 1,
      status: typeof record.status === 'string' ? record.status : 'unknown',
    })
  }
  return result
}

function collectPhaseCycles(phaseState: PhaseState | undefined): CycleRecord[] {
  if (!phaseState) return []
  return [
    ...readCycleList(phaseState.fix_cycles, 'fix'),
    ...readCycleList(phaseState.review_fix_cycles, 'review-fix'),
    ...readCycleList(phaseState.gate_cycles, 'gate'),
    ...readCycleList(phaseState.cycles, 'cycle'),
  ]
}

function groupCyclesByKind(records: CycleRecord[]): Map<string, CycleRecord[]> {
  const groups = new Map<string, CycleRecord[]>()
  for (const record of records) {
    const list = groups.get(record.kind) || []
    list.push(record)
    groups.set(record.kind, list)
  }
  return groups
}

function cycleNodeLabel(record: CycleRecord): string {
  return record.kind === 'cycle' ? `cycle ${record.cycle}` : `${record.kind} cycle ${record.cycle}`
}

interface TimelinePlan {
  kind: 'review' | 'fix'
  cycleIndex: number
  record?: CycleRecord
}

function planTimeline(cycleList: CycleRecord[]): TimelinePlan[] {
  const plan: TimelinePlan[] = [{ kind: 'review', cycleIndex: 0 }]
  for (const record of cycleList) {
    plan.push({ kind: 'fix', cycleIndex: record.cycle, record })
    plan.push({ kind: 'review', cycleIndex: record.cycle })
  }
  return plan
}

const TIMELINE_PHASE_LABEL: Record<string, string> = {
  review: 'review',
  verify: 'verify',
  gate: 'gate',
  'scenario-execute': 'scenario',
}

function buildPhaseTimelineNodes(
  phaseState: PhaseState | undefined,
  qualifiedTrack: string,
  phase: string,
  cycleList: CycleRecord[],
  phaseLabel = phase,
): TreeNode[] {
  if (cycleList.length === 0) return []
  const plan = planTimeline(cycleList)
  const overallStatus = phaseState?.status || 'unknown'
  const reviewStepCount = plan.filter(step => step.kind === 'review').length
  let reviewOrdinal = 0
  return plan.map((step, position) => {
    const isFix = step.kind === 'fix'
    const record = step.record
    let label: string
    if (isFix && record) {
      label = `${record.kind} cycle ${record.cycle}`
    } else {
      reviewOrdinal += 1
      label = `${phaseLabel} #${reviewOrdinal} / ${reviewStepCount}`
    }
    const status = isFix && record ? record.status : overallStatus
    return {
      id: `${qualifiedTrack}:${phase}.step.${position}.${step.kind}-${step.cycleIndex}`,
      label,
      type: 'cycle-step' as const,
      status,
      children: [],
      meta: {
        stepKind: step.kind,
        stepPosition: position,
        cycleIndex: step.cycleIndex,
        cycleKind: record?.kind || null,
        cycle: record?.cycle ?? 0,
        parentTrack: qualifiedTrack,
        parentPhase: phase,
        phaseState,
        totalSteps: plan.length,
      },
    }
  })
}

function buildPhaseCycleNodes(
  phaseState: PhaseState | undefined,
  qualifiedTrack: string,
  phase: string,
): TreeNode[] {
  const records = collectPhaseCycles(phaseState)
  if (records.length === 0) return []
  const phaseLabel = TIMELINE_PHASE_LABEL[phase] || phase
  const groups = groupCyclesByKind(records)
  const children: TreeNode[] = []
  for (const [kind, list] of groups) {
    const timeline = buildPhaseTimelineNodes(phaseState, qualifiedTrack, phase, list, phaseLabel)
    if (timeline.length > 0) {
      children.push({
        id: `${qualifiedTrack}:${phase}.group.${kind}`,
        label: `${kind} 循环 (${list.length})`,
        type: 'cycle-group',
        status: list.every(r => r.status === 'completed' || r.status === 'pass') ? 'completed' : 'in_progress',
        children: timeline,
        meta: { kind, parentTrack: qualifiedTrack, parentPhase: phase },
      })
    }
  }
  return children
}

const PHASE_ORDER = ['test', 'dev', 'review', 'verify', 'gate', 'simple', 'scenario-execute', 'scenario-fix']

function phaseOrder(phase: string): number {
  const index = PHASE_ORDER.indexOf(phase)
  return index === -1 ? PHASE_ORDER.length : index
}

export function buildTree(manifest: Manifest, snapshot: Snapshot | null): TreeNode[] {
  const stages: TreeNode[] = []

  for (const stage of manifest.stages) {
    const trackNodes: TreeNode[] = []

    for (const track of stage.tracks) {
      if (!track.enabled) continue
      const qualifiedTrack = `${stage.name}.${track.id}`
      const trackState = findTrackState(snapshot, qualifiedTrack)
      const phaseNodes: TreeNode[] = []

      const prompts = track.phase_prompts || {}
      const sortedPhases = Object.keys(prompts).sort(
        (a, b) => phaseOrder(a) - phaseOrder(b)
      )

      for (const phase of sortedPhases) {
        const status = phaseStatus(trackState, phase)
        const subPipelines = findSubPipelinesForPhase(trackState, phase)

        const children: TreeNode[] = []
        if (subPipelines.length > 0) {
          const spNodes = buildSubPipelineNodes(subPipelines)
          children.push(...spNodes)
        } else if (trackState?.phases[phase]) {
          children.push(...buildPhaseCycleNodes(trackState.phases[phase], qualifiedTrack, phase))
        }

        const phaseMeta: Record<string, unknown> = {
          track: qualifiedTrack,
          phase,
          ...(trackState?.phases[phase] ? { phaseState: trackState.phases[phase] } : {}),
        }

        phaseNodes.push({
          id: `${qualifiedTrack}:${phase}`,
          label: phase,
          type: 'phase',
          status,
          children,
          meta: phaseMeta,
        })
      }

      const trackStatus = trackState?.status || 'pending'
      trackNodes.push({
        id: qualifiedTrack,
        label: track.id,
        type: 'track',
        status: trackStatus,
        children: phaseNodes,
        meta: { trackState, type: track.type, qualifiedTrack },
      })
    }

    stages.push({
      id: stage.name,
      label: `${stage.name} (${stage.environment})`,
      type: 'stage',
      status: aggregateStatuses(trackNodes.map(track => track.status)),
      children: trackNodes,
      meta: { environment: stage.environment },
    })
  }

  if (manifest.final_gate) {
    const finalGateState = findTrackState(snapshot, 'final-gate')
    const gatePhaseState = finalGateState?.phases?.gate
    const finalGateNode: TreeNode = {
      id: 'final-gate',
      label: 'final-gate',
      type: 'final-gate',
      status: finalGateStatus(snapshot),
      children: [],
      meta: {
        track: 'final-gate',
        phase: 'gate',
        trackState: finalGateState,
        phaseState: gatePhaseState,
      },
    }
    stages.push({
      id: 'final',
      label: 'final',
      type: 'stage',
      status: finalGateNode.status,
      children: [finalGateNode],
      meta: {},
    })
  }

  return stages
}

export function findNodeById(nodes: TreeNode[], id: string): TreeNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    if (node.children.length > 0) {
      const found = findNodeById(node.children, id)
      if (found) return found
    }
  }
  return null
}

export function findFirstInProgress(nodes: TreeNode[]): string | null {
  for (const node of nodes) {
    const childMatch = findFirstInProgress(node.children)
    if (childMatch) return childMatch
    if (node.status === 'in_progress' || node.status === 'running') return node.id
  }
  return null
}

function pathToNode(nodes: TreeNode[], id: string, ancestors: string[] = []): string[] | null {
  for (const node of nodes) {
    const path = [...ancestors, node.id]
    if (node.id === id) return path
    const childPath = pathToNode(node.children, id, path)
    if (childPath) return childPath
  }
  return null
}

function expandTrackToLeaves(set: Set<string>, track: TreeNode): void {
  set.add(track.id)
  for (const phase of track.children) {
    set.add(phase.id)
    for (const cycle of phase.children) {
      set.add(cycle.id)
      for (const sub of cycle.children) {
        set.add(sub.id)
      }
    }
  }
}

export function buildAutoExpandSet(nodes: TreeNode[], currentTrack?: string): Set<string> {
  const set = new Set<string>()
  const inProgressId = findFirstInProgress(nodes)
  const activePath = inProgressId ? pathToNode(nodes, inProgressId) || [] : []
  for (const id of activePath.slice(0, -1)) set.add(id)
  let activeTrackId = activePath.length >= 2 ? activePath[1] : null
  if (currentTrack) {
    for (const stage of nodes) {
      if (stage.children.some(track => track.id === currentTrack)) {
        activeTrackId = currentTrack
        break
      }
    }
  }
  for (const stage of nodes) {
    set.add(stage.id)
    for (const track of stage.children) {
      if (track.id === activeTrackId) {
        expandTrackToLeaves(set, track)
        continue
      }
      if (track.status === 'completed' || track.status === 'pending') continue
      set.add(track.id)
      for (const phase of track.children) {
        if (phase.id === inProgressId || phase.status === 'in_progress' || phase.status === 'running') {
          set.add(phase.id)
          for (const fc of phase.children) {
            set.add(fc.id)
          }
        }
        if (phase.children.length > 0) {
          for (const fc of phase.children) {
            if (fc.status === 'in_progress' || fc.status === 'running') {
              set.add(fc.id)
            }
          }
        }
      }
    }
  }
  return set
}
