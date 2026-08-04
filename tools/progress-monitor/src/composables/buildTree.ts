import type { Manifest, Snapshot, TreeNode, TrackState, PhaseState, SubPipelineInfo } from '@/types/pipeline'

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

function findFixedPhaseStatus(trackState: TrackState, phase: string): string {
  const ps = trackState.phases[phase]
  if (!ps) return 'pending'
  return ps.status
}

function extractBareTrack(qualifiedTrack: string): string {
  return qualifiedTrack.includes('.') ? qualifiedTrack.split('.').pop()! : qualifiedTrack
}

function buildSubPipelineNodes(phases: SubPipelineInfo[], trackState: TrackState): TreeNode[] {
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
    nodes.push({
      id: `${sp.parent_track}.${sp.parent_phase}.${sp.kind}-${sp.cycle}`,
      label: cycleLabel,
      type: 'fix-cycle',
      status: children.every(c => c.status === 'completed') ? 'completed' : 'in_progress',
      children,
      meta: { spKind: sp.kind, cycle: sp.cycle, parentTrack: sp.parent_track, parentPhase: sp.parent_phase },
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
        (a, b) => PHASE_ORDER.indexOf(a) - PHASE_ORDER.indexOf(b)
      )

      for (const phase of sortedPhases) {
        const status = phaseStatus(trackState, phase)
        const subPipelines = findSubPipelinesForPhase(trackState, phase)

        const children: TreeNode[] = []
        if (subPipelines.length > 0) {
          const spNodes = buildSubPipelineNodes(subPipelines, trackState!)
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
      status: 'pending',
      children: trackNodes,
      meta: { environment: stage.environment },
    })
  }

  if (manifest.final_gate) {
    const finalGateNode: TreeNode = {
      id: 'final-gate',
      label: 'final-gate',
      type: 'final-gate',
      status: phaseStatus(
        snapshot ? snapshot.tracks['final-gate'] as unknown as TrackState : undefined,
        'gate'
      ),
      children: [],
      meta: { tasks_md_section: manifest.final_gate.tasks_md_section },
    }
    stages.push({
      id: 'final',
      label: 'final',
      type: 'stage',
      status: 'pending',
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
    if (node.status === 'in_progress' || node.status === 'running') return node.id
    for (const child of node.children) {
      if (child.status === 'in_progress' || child.status === 'running') return child.id
      for (const grandchild of child.children) {
        if (grandchild.status === 'in_progress' || grandchild.status === 'running') return grandchild.id
      }
    }
  }
  return null
}

export function buildAutoExpandSet(nodes: TreeNode[]): Set<string> {
  const set = new Set<string>()
  const inProgressId = findFirstInProgress(nodes)
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