export type ParsedHash = {
  view: 'dashboard' | 'form' | 'canvas' | 'diff'
  selection: {
    module?: string
    env?: string
    track?: string
    stage?: string
    field?: string
  }
}

export function parseHash(hash: string, search: string): ParsedHash {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const viewParam = params.get('view')
  const view: ParsedHash['view'] = (viewParam as ParsedHash['view']) || 'dashboard'

  const fragment = hash.startsWith('#') ? hash.slice(1) : hash

  const selection: ParsedHash['selection'] = {}
  const moduleParam = params.get('module')
  if (moduleParam) selection.module = moduleParam
  const envParam = params.get('env')
  if (envParam) selection.env = envParam
  const trackParam = params.get('track')
  if (trackParam) selection.track = trackParam
  const stageParam = params.get('stage')
  if (stageParam) selection.stage = stageParam

  if (fragment) selection.field = fragment

  return { view, selection }
}

export function buildHash(parsed: ParsedHash): string {
  const params = new URLSearchParams()
  if (parsed.view && parsed.view !== 'form') params.set('view', parsed.view)
  if (parsed.selection.module) params.set('module', parsed.selection.module)
  if (parsed.selection.env) params.set('env', parsed.selection.env)
  if (parsed.selection.track) params.set('track', parsed.selection.track)
  if (parsed.selection.stage) params.set('stage', parsed.selection.stage)
  const q = params.toString()
  const f = parsed.selection.field ? `#${parsed.selection.field}` : ''
  return `${q ? '?' + q : ''}${f}`
}

export function applyHash(parsed: ParsedHash): void {
  const h = buildHash(parsed)
  if (h !== window.location.hash + window.location.search) {
    window.history.replaceState(null, '', window.location.pathname + h)
  }
}