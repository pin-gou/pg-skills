const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, ch => ESCAPE_MAP[ch])
}

const UNSAFE_SCHEME_RE = /^[a-z][a-z0-9+.-]*:/i
const SAFE_SCHEME_RE = /^(?:https?:|mailto:)/i

function safeUrl(value: string): string {
  const url = value.trim()
  if (UNSAFE_SCHEME_RE.test(url) && !SAFE_SCHEME_RE.test(url)) return '#'
  return url
}

function inline(source: string): string {
  return source
    .replace(/!\[([^\]]+)\]\(([^)\s]+)\)/g, (_, alt: string, url: string) =>
      `<img alt="${escapeHtml(alt)}" src="${escapeHtml(safeUrl(url))}" />`)
    .replace(/`([^`]+)`/g, (_, code: string) => `<code>${code}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, (_, bold: string) => `<strong>${bold}</strong>`)
    .replace(/(^|[^*])\*([^*\n]+)\*/g, (_, before: string, em: string) => `${before}<em>${em}</em>`)
    .replace(/~~([^~]+)~~/g, (_, del: string) => `<del>${del}</del>`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label: string, url: string) =>
      `<a href="${escapeHtml(safeUrl(url))}">${label}</a>`)
}

function isTableSeparator(row: string[]): boolean {
  return row.length > 0 && row.every(cell => /^:?-{1,}:?$/.test(cell))
}

function parseTableRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
}

function renderTable(rows: string[][]): string {
  if (rows.length === 0) return ''
  const hasSeparator = rows.length >= 2 && isTableSeparator(rows[1])
  let head = ''
  let body: string[][]
  if (hasSeparator) {
    head = `<thead><tr>${rows[0].map(cell => `<th>${inline(escapeHtml(cell))}</th>`).join('')}</tr></thead>`
    body = rows.slice(2)
  } else {
    body = rows
  }
  const bodyHtml = body.length > 0
    ? `<tbody>${body.map(row => `<tr>${row.map(cell => `<td>${inline(escapeHtml(cell))}</td>`).join('')}</tr>`).join('')}</tbody>`
    : ''
  return `<table>${head}${bodyHtml}</table>`
}

/**
 * Lightweight markdown → HTML renderer for read-only artifact preview.
 * Input is escaped first, so no raw HTML from the document is emitted.
 */
export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let paragraph: string[] = []
  let fenceLines: string[] | null = null

  function flushParagraph() {
    if (paragraph.length > 0) {
      out.push(`<p>${inline(escapeHtml(paragraph.join(' ')))}</p>`)
      paragraph = []
    }
  }

  let index = 0
  while (index < lines.length) {
    const raw = lines[index]
    const line = raw.trimEnd()

    if (fenceLines !== null) {
      if (/^```/.test(line.trim())) {
        out.push(`<pre class="code-block"><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`)
        fenceLines = null
      } else {
        fenceLines.push(line)
      }
      index += 1
      continue
    }

    const fence = /^```(\S*)\s*$/.exec(line.trim())
    if (fence) {
      flushParagraph()
      fenceLines = []
      index += 1
      continue
    }

    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    if (heading) {
      flushParagraph()
      const level = heading[1].length
      out.push(`<h${level}>${inline(escapeHtml(heading[2]))}</h${level}>`)
      index += 1
      continue
    }

    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
      flushParagraph()
      out.push('<hr />')
      index += 1
      continue
    }

    const quote = /^>\s?(.*)$/.exec(line)
    if (quote) {
      flushParagraph()
      out.push(`<blockquote>${inline(escapeHtml(quote[1]))}</blockquote>`)
      index += 1
      continue
    }

    if (/^\s*\|/.test(line)) {
      flushParagraph()
      const rows: string[][] = []
      while (index < lines.length && /^\s*\|/.test(lines[index])) {
        rows.push(parseTableRow(lines[index]))
        index += 1
      }
      out.push(renderTable(rows))
      continue
    }

    const ul = /^\s*[-*+]\s+(.*)$/.exec(line)
    if (ul) {
      flushParagraph()
      const items: string[] = [ul[1]]
      index += 1
      while (index < lines.length) {
        const next = /^\s*[-*+]\s+(.*)$/.exec(lines[index].trimEnd())
        if (!next) break
        items.push(next[1])
        index += 1
      }
      out.push(`<ul>${items.map(item => `<li>${inline(escapeHtml(item))}</li>`).join('')}</ul>`)
      continue
    }

    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (ol) {
      flushParagraph()
      const items: string[] = [ol[1]]
      index += 1
      while (index < lines.length) {
        const next = /^\s*\d+[.)]\s+(.*)$/.exec(lines[index].trimEnd())
        if (!next) break
        items.push(next[1])
        index += 1
      }
      out.push(`<ol>${items.map(item => `<li>${inline(escapeHtml(item))}</li>`).join('')}</ol>`)
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      index += 1
      continue
    }

    paragraph.push(line.trim())
    index += 1
  }

  if (fenceLines !== null) {
    out.push(`<pre class="code-block"><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`)
  }
  flushParagraph()
  return out.join('\n')
}
