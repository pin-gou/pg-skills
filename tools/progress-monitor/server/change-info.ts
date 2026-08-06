import fs from 'node:fs'
import path from 'node:path'
import { countSnapshotPhases } from '../src/shared/pipelineStatus.ts'

export interface ChangeInfoRecord {
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

export function readFileOrNull(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, 'utf-8')
  } catch {
    return null
  }
}

export function parseJsonFile(filePath: string): { value: Record<string, unknown> | null; error: string | null } {
  const text = readFileOrNull(filePath)
  if (text === null) return { value: null, error: null }
  try {
    return { value: JSON.parse(text) as Record<string, unknown>, error: null }
  } catch (error) {
    return { value: null, error: `Invalid JSON in ${path.basename(filePath)}: ${String(error)}` }
  }
}

export function latestMtime(paths: string[]): string | null {
  let latest = 0
  for (const filePath of paths) {
    try {
      latest = Math.max(latest, fs.statSync(filePath).mtimeMs)
    } catch {
      // Optional files do not contribute to the timestamp.
    }
  }
  return latest > 0 ? new Date(latest).toISOString() : null
}

export function latestEventTimestamp(changeRoot: string): string | null {
  const text = readFileOrNull(path.join(changeRoot, '2-build', 'pipeline.events'))
  if (!text) return null
  const lines = text.trim().split(/\r?\n/).filter(Boolean)
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const event = JSON.parse(lines[index]) as { timestamp?: unknown; ts?: unknown }
      const timestamp = event.timestamp ?? event.ts
      if (typeof timestamp === 'string') return timestamp
    } catch {
      // Continue backwards to the latest valid event.
    }
  }
  return null
}

export function changeInfo(
  name: string,
  changeRoot: string,
  isActive: boolean,
  stallThresholdMs: number,
  now = Date.now(),
): ChangeInfoRecord {
  const manifestPath = path.join(changeRoot, 'execution-manifest.yaml')
  const snapshotPath = path.join(changeRoot, '2-build', 'pipeline.snapshot.json')
  const eventsPath = path.join(changeRoot, '2-build', 'pipeline.events')
  const hasManifest = fs.existsSync(manifestPath)
  const hasSnapshot = fs.existsSync(snapshotPath)
  const parsed = hasSnapshot ? parseJsonFile(snapshotPath) : { value: null, error: null }
  const snapshot = parsed.value
  const progress = countSnapshotPhases(snapshot)
  const mtime = latestMtime([manifestPath, snapshotPath, eventsPath])
  const status = typeof snapshot?.status === 'string' ? snapshot.status : null
  const running = status === 'running' || status === 'in_progress'
  const parsedMtime = mtime ? Date.parse(mtime) : Number.NaN
  const isStalled = Boolean(
    isActive && running && Number.isFinite(parsedMtime) && now - parsedMtime > stallThresholdMs,
  )

  return {
    name,
    isActive,
    hasManifest,
    hasSnapshot,
    snapshotStatus: status,
    currentStage: typeof snapshot?.current_stage === 'string' ? snapshot.current_stage : null,
    currentTrack: typeof snapshot?.current_track === 'string' ? snapshot.current_track : null,
    currentPhase: typeof snapshot?.current_phase === 'string' ? snapshot.current_phase : null,
    failedReason: typeof snapshot?.failed_reason === 'string' ? snapshot.failed_reason : null,
    completedPhases: progress.completed,
    totalPhases: progress.total,
    lastEventAt: latestEventTimestamp(changeRoot),
    isStalled,
    mtime,
    parseError: parsed.error,
  }
}

export function listChanges(changesRoot: string, stallThresholdMs: number): ChangeInfoRecord[] {
  const result: ChangeInfoRecord[] = []
  if (fs.existsSync(changesRoot)) {
    for (const entry of fs.readdirSync(changesRoot, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name === 'archive') continue
      result.push(changeInfo(entry.name, path.join(changesRoot, entry.name), true, stallThresholdMs))
    }
  }

  const archiveRoot = path.join(changesRoot, 'archive')
  if (fs.existsSync(archiveRoot)) {
    for (const entry of fs.readdirSync(archiveRoot, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      result.push(changeInfo(entry.name, path.join(archiveRoot, entry.name), false, stallThresholdMs))
    }
  }

  return result.sort((left, right) => {
    if (left.isActive !== right.isActive) return left.isActive ? -1 : 1
    return (right.mtime ?? '').localeCompare(left.mtime ?? '')
  })
}
