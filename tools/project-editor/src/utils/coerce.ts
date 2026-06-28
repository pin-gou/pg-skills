export function s(v: unknown): string {
  if (v === null || v === undefined) return ''
  return String(v)
}

export function sa(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String)
  return []
}