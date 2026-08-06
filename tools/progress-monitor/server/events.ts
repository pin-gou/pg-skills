export function paginateNewest<T>(items: T[], page: number, size: number): T[] {
  const safePage = Math.max(1, page)
  const safeSize = Math.max(1, size)
  const start = (safePage - 1) * safeSize
  return items.slice().reverse().slice(start, start + safeSize)
}

function eventField(event: Record<string, unknown>, key: string): unknown {
  const direct = event[key]
  if (direct !== undefined && direct !== null && direct !== '') return direct
  const data = event.data
  return data !== null && typeof data === 'object' && !Array.isArray(data)
    ? (data as Record<string, unknown>)[key]
    : undefined
}

function eventSearchText(event: Record<string, unknown>): string {
  return [
    event.type,
    event.ts,
    event.timestamp,
    eventField(event, 'track'),
    eventField(event, 'phase'),
    eventField(event, 'summary'),
    eventField(event, 'status'),
    eventField(event, 'message'),
    eventField(event, 'agent'),
  ]
    .filter(value => value !== undefined && value !== null)
    .join(' ')
}

function eventIdentifiers(event: Record<string, unknown>): string[] {
  return [event.type, eventField(event, 'track'), eventField(event, 'phase')]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .map(value => value.toLowerCase())
}

export function isFailureEvent(event: Record<string, unknown>): boolean {
  const searchable = [
    event.type,
    eventField(event, 'status'),
    eventField(event, 'summary'),
    eventField(event, 'message'),
  ]
    .filter(value => value !== undefined && value !== null)
    .join(' ')
  return /(fail|error|abandon|escalat)/i.test(searchable)
}

export function filterEvents<T extends Record<string, unknown>>(
  items: T[],
  search = '',
  failuresOnly = false,
): T[] {
  const query = search.trim().toLowerCase()
  const candidates = failuresOnly ? items.filter(isFailureEvent) : items
  if (!query) return candidates

  const hasExactIdentifier = candidates.some(event => eventIdentifiers(event).includes(query))
  return candidates.filter(event => hasExactIdentifier
    ? eventIdentifiers(event).includes(query)
    : eventSearchText(event).toLowerCase().includes(query))
}
