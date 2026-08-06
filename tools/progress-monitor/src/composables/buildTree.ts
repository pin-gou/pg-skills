import type { Manifest, Snapshot, TreeNode, TrackState, SubPipelineInfo } from '../types/pipeline.ts'
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

export function buildAutoExpandSet(nodes: TreeNode[]): Set<string> {
  const set = new Set<string>()
  const inProgressId = findFirstInProgress(nodes)
  const activePath = inProgressId ? pathToNode(nodes, inProgressId) || [] : []
  for (const id of activePath.slice(0, -1)) set.add(id)
  for (const stage of nodes) {
    set.add(stage.id)
    for (const track of stage.children) {
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
