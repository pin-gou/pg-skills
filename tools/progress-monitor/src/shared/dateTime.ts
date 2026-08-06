const ISO_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/

export function formatPipelineTimestamp(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) return '-'
  const match = ISO_TIMESTAMP.exec(value)
  if (!match) return value
  return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`
}
