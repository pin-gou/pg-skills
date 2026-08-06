const COMPLETED_STATUSES = new Set(['completed', 'pass', 'skipped'])

export function isCompletedStatus(status: unknown): boolean {
  return typeof status === 'string' && COMPLETED_STATUSES.has(status)
}

export function countSnapshotPhases(snapshot: unknown): { completed: number; total: number } {
  if (!snapshot || typeof snapshot !== 'object') return { completed: 0, total: 0 }
  const tracks = (snapshot as { tracks?: unknown }).tracks
  if (!tracks || typeof tracks !== 'object') return { completed: 0, total: 0 }

  let completed = 0
  let total = 0
  for (const track of Object.values(tracks as Record<string, unknown>)) {
    if (!track || typeof track !== 'object') continue
    const phases = (track as { phases?: unknown }).phases
    if (!phases || typeof phases !== 'object') continue
    for (const phase of Object.values(phases as Record<string, unknown>)) {
      if (!phase || typeof phase !== 'object') continue
      total += 1
      if (isCompletedStatus((phase as { status?: unknown }).status)) completed += 1
    }
  }
  return { completed, total }
}
