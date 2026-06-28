export type DiffEntryKind = '+' | '-' | '~'

export type DiffEntry = {
  path: string
  kind: DiffEntryKind
  before?: unknown
  after?: unknown
}

function isObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function diffInternal(before: unknown, after: unknown, basePath: string, out: DiffEntry[]): void {
  if (before === after) return

  if (Array.isArray(before) || Array.isArray(after)) {
    const bArr = Array.isArray(before) ? before : []
    const aArr = Array.isArray(after) ? after : []
    const maxLen = Math.max(bArr.length, aArr.length)
    if (bArr.length !== aArr.length) {
      out.push({ path: basePath, kind: '~', before: bArr, after: aArr })
    }
    for (let i = 0; i < maxLen; i++) {
      diffInternal(bArr[i], aArr[i], `${basePath}[${i}]`, out)
    }
    return
  }

  if (isObject(before) && isObject(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)])
    for (const k of keys) {
      const childPath = basePath ? `${basePath}.${k}` : k
      diffInternal(before[k], after[k], childPath, out)
    }
    return
  }

  if (before === undefined) {
    out.push({ path: basePath, kind: '+', after })
  } else if (after === undefined) {
    out.push({ path: basePath, kind: '-', before })
  } else {
    out.push({ path: basePath, kind: '~', before, after })
  }
}

export function diffFields(before: unknown, after: unknown): DiffEntry[] {
  const out: DiffEntry[] = []
  diffInternal(before, after, '', out)
  return out
}

export function diffCount(before: unknown, after: unknown): { add: number; del: number; mod: number } {
  const entries = diffFields(before, after)
  let add = 0, del = 0, mod = 0
  for (const e of entries) {
    if (e.kind === '+') add++
    else if (e.kind === '-') del++
    else mod++
  }
  return { add, del, mod }
}