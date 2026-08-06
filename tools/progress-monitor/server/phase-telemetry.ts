type JsonObject = Record<string, unknown>

interface PhaseTelemetry {
  agent?: string
  startedAt?: string
  completedAt?: string
  status?: string
  summary?: string
  reportPath?: string
}

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function eventPayload(event: JsonObject): JsonObject {
  return asObject(event.data) ?? event
}

/**
 * Reconstruct phase execution metadata from the append-only event stream.
 * A later dispatch represents a retry, so it replaces the previous attempt.
 */
export function phaseTelemetryFromEvents(events: unknown[]): Map<string, PhaseTelemetry> {
  const telemetry = new Map<string, PhaseTelemetry>()

  for (const rawEvent of events) {
    const event = asObject(rawEvent)
    if (!event) continue

    const type = stringValue(event.type)
    if (type !== 'dispatch_started' && type !== 'record_received') continue

    const payload = eventPayload(event)
    const track = stringValue(payload.track)
    const phase = stringValue(payload.phase)
    if (!track || !phase) continue

    const key = `${track}\u0000${phase}`
    const timestamp = stringValue(event.ts) ?? stringValue(event.timestamp)

    if (type === 'dispatch_started') {
      telemetry.set(key, {
        agent: stringValue(payload.agent),
        startedAt: timestamp,
      })
      continue
    }

    const current = telemetry.get(key) ?? {}
    telemetry.set(key, {
      ...current,
      completedAt: timestamp,
      status: stringValue(payload.status),
      summary: stringValue(payload.summary),
      reportPath: stringValue(payload.report_path),
    })
  }

  return telemetry
}

/**
 * Return an enriched copy for display. Snapshot values remain authoritative;
 * event-derived values are only used when the runner left a field empty.
 */
export function enrichSnapshotPhaseTelemetry<T>(snapshot: T, events: unknown[]): T {
  const root = asObject(snapshot)
  const tracks = asObject(root?.tracks)
  if (!root || !tracks) return snapshot

  const telemetry = phaseTelemetryFromEvents(events)
  const enrichedTracks: JsonObject = { ...tracks }

  for (const [trackId, rawTrack] of Object.entries(tracks)) {
    const track = asObject(rawTrack)
    const phases = asObject(track?.phases)
    if (!track || !phases) continue

    const enrichedPhases: JsonObject = { ...phases }
    for (const [phaseName, rawPhase] of Object.entries(phases)) {
      const phase = asObject(rawPhase)
      const derived = telemetry.get(`${trackId}\u0000${phaseName}`)
      if (!phase || !derived) continue

      enrichedPhases[phaseName] = {
        ...phase,
        agent: stringValue(phase.agent) ?? derived.agent ?? null,
        started_at: stringValue(phase.started_at) ?? derived.startedAt ?? null,
        completed_at: stringValue(phase.completed_at) ?? derived.completedAt ?? null,
      }
    }

    enrichedTracks[trackId] = { ...track, phases: enrichedPhases }
  }

  const finalGate = telemetry.get('final-gate\u0000gate')
  if (!enrichedTracks['final-gate'] && finalGate) {
    const fallbackStatus = root.status === 'completed' ? 'pass' : 'in_progress'
    enrichedTracks['final-gate'] = {
      track_id: 'final-gate',
      bare: 'final-gate',
      label: '最终门控审查',
      status: finalGate.status ?? fallbackStatus,
      started_at: finalGate.startedAt ?? null,
      completed_at: finalGate.completedAt ?? null,
      modules: [],
      phases: {
        gate: {
          status: finalGate.status ?? fallbackStatus,
          attempt: 1,
          started_at: finalGate.startedAt ?? null,
          completed_at: finalGate.completedAt ?? null,
          agent: finalGate.agent ?? null,
          report_path: finalGate.reportPath ?? null,
          summary: finalGate.summary ?? '',
          tasks_marked: [],
          cycles: [],
          fix_cycles: [],
          review_fix_cycles: [],
          gate_cycles: [],
          current_cycle: 1,
        },
      },
      sub_pipelines: [],
      code_review_enabled: false,
      verify_enabled: false,
      gate_enabled: true,
      scenario_last_restart_attempt: 0,
    }
  }

  return { ...root, tracks: enrichedTracks } as T
}
