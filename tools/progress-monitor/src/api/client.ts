import type { ChangeInfo, Manifest, Snapshot, EventsResponse, PipelineEvent } from '@/types/pipeline'

const BASE = ''

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.json()
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.text()
}

export const api = {
  listChanges: (): Promise<ChangeInfo[]> =>
    fetchJson('/__pg/changes'),

  getManifest: (change: string): Promise<Manifest> =>
    fetchJson(`/__pg/manifest/${encodeURIComponent(change)}`),

  getSnapshot: (change: string): Promise<Snapshot> =>
    fetchJson(`/__pg/snapshot/${encodeURIComponent(change)}`),

  getEvents: (change: string, page = 1, size = 50): Promise<EventsResponse> =>
    fetchJson(`/__pg/events/${encodeURIComponent(change)}?page=${page}&size=${size}`),

  getArtifactContent: (change: string, path: string): Promise<string> =>
    fetchText(`/__pg/artifact/${encodeURIComponent(change)}?path=${encodeURIComponent(path)}`),

  listArtifacts: (change: string, track: string, phase: string): Promise<string[]> =>
    fetchJson(`/__pg/artifacts/${encodeURIComponent(change)}?track=${encodeURIComponent(track)}&phase=${encodeURIComponent(phase)}`),

  getRawYaml: (change: string, file: string): Promise<string> =>
    fetchText(`/.pg/changes/${encodeURIComponent(change)}/${file}`),
}