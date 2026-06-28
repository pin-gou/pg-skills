import { parseDocument, stringify, type Document, type YAMLMap, type Scalar, type Pair, isMap, isScalar } from 'yaml'

let docCache: Document.Parsed | null = null
let sourceText = ''

export function parseYaml(text: string): object | null {
  sourceText = text
  docCache = parseDocument(text, { keepSourceTokens: true })
  if (!docCache) return null
  return docCache.toJS() as object | null
}

export function dumpYaml(data: object, originalText?: string): string {
  if (originalText) sourceText = originalText
  try {
    const doc = parseDocument(sourceText)
    const root = doc.contents as YAMLMap | null
    if (isMap(root)) {
      mergeIntoMap(root, data as Record<string, unknown>)
    }
    return String(doc)
  } catch {
    return stringify(data, { indent: 2, lineWidth: 120, sortMapEntries: false })
  }
}

function mergeIntoMap(map: YAMLMap, data: Record<string, unknown>): void {
  for (const [key, val] of Object.entries(data)) {
    const existing = map.items.find(item => isScalar(item.key) && String(item.key) === key) as Pair<Scalar, unknown> | undefined
    if (existing && existing.value !== undefined) {
      existing.value = toYamlValue(val)
    } else {
      map.add({ key, value: toYamlValue(val) })
    }
  }
}

function toYamlValue(val: unknown): unknown {
  if (val === null || val === undefined) return val
  if (typeof val === 'object' && !Array.isArray(val)) {
    const m = new Map()
    for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
      m.set(k, toYamlValue(v))
    }
    return m
  }
  return val
}

export function getSourceText(): string {
  return sourceText
}