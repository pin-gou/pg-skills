import type { ChangeInfo, Manifest, Snapshot, EventsResponse, PipelineEvent } from '@/types/pipeline'

const BASE = ''

export class HttpError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json() as { error?: string; detail?: string }
    return [body.error, body.detail].filter(Boolean).join(': ') || res.statusText
  } catch {
    return res.statusText
  }
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new HttpError(res.status, `HTTP ${res.status}: ${await errorMessage(res)}`)
  }
  return res.json()
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) {
    throw new HttpError(res.status, `HTTP ${res.status}: ${await errorMessage(res)}`)
  }
  return res.text()
}

export const api = {
  listChanges: (): Promise<ChangeInfo[]> =>
    fetchJson('/__pg/changes'),

  getManifest: (change: string): Promise<Manifest> =>
    fetchJson(`/__pg/manifest/${encodeURIComponent(change)}`),

  getManifestRaw: (change: string): Promise<string> =>
    fetchText(`/__pg/manifest-raw/${encodeURIComponent(change)}`),

  getSnapshot: (change: string): Promise<Snapshot> =>
    fetchJson(`/__pg/snapshot/${encodeURIComponent(change)}`),

  getEvents: (
    change: string,
    page = 1,
    size = 50,
    search = '',
    failuresOnly = false,
  ): Promise<EventsResponse> => {
    const params = new URLSearchParams({ page: String(page), size: String(size) })
    if (search) params.set('q', search)
    if (failuresOnly) params.set('failures', 'true')
    return fetchJson(`/__pg/events/${encodeURIComponent(change)}?${params}`)
  },

  getArtifactContent: (change: string, path: string): Promise<string> =>
    fetchText(`/__pg/artifact/${encodeURIComponent(change)}?path=${encodeURIComponent(path)}`),

  listArtifacts: (change: string, track: string, phase: string): Promise<string[]> =>
    fetchJson(`/__pg/artifacts/${encodeURIComponent(change)}?track=${encodeURIComponent(track)}&phase=${encodeURIComponent(phase)}`),
}
