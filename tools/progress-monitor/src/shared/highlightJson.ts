import { escapeHtml } from './markdown.ts'

const JSON_TOKEN_RE =
  /("(?:[^"\\]|\\.)*")(\s*:)?|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

/**
 * Syntax-highlight a JSON document for read-only preview.
 * Non-token text (whitespace, punctuation) is emitted escaped verbatim.
 */
export function highlightJson(source: string): string {
  let out = ''
  let last = 0
  let match: RegExpExecArray | null
  const re = new RegExp(JSON_TOKEN_RE.source, 'g')
  while ((match = re.exec(source)) !== null) {
    out += escapeHtml(source.slice(last, match.index))
    if (match[1] !== undefined) {
      if (match[2] !== undefined) {
        out += `<span class="j-key">${escapeHtml(match[1])}</span>${escapeHtml(match[2])}`
      } else {
        out += `<span class="j-string">${escapeHtml(match[1])}</span>`
      }
    } else if (match[3] !== undefined) {
      out += `<span class="j-keyword">${escapeHtml(match[3])}</span>`
    } else if (match[4] !== undefined) {
      out += `<span class="j-number">${escapeHtml(match[4])}</span>`
    }
    last = re.lastIndex
  }
  out += escapeHtml(source.slice(last))
  return out
}

/**
 * Pretty-print a JSON document. Valid JSON is reformatted via JSON.parse;
 * anything else (comments, trailing commas, BOM, partial output) is passed
 * through a structural indenter that preserves string contents verbatim.
 */
export function formatJson(source: string): string {
  const text = source.replace(/^\uFEFF/, '')
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return indentJsonLike(text)
  }
}

const JSON_STRUCTURAL = new Set(['{', '}', '[', ']', ',', ':'])

function indentJsonLike(source: string): string {
  let out = ''
  let indent = 0
  let index = 0
  const length = source.length
  while (index < length) {
    const ch = source[index]
    if (ch === '"') {
      let end = index + 1
      while (end < length) {
        if (source[end] === '\\') {
          end += 2
          continue
        }
        if (source[end] === '"') {
          end += 1
          break
        }
        end += 1
      }
      out += source.slice(index, end)
      index = end
      continue
    }
    if (ch === '{' || ch === '[') {
      out += `${ch}\n${'  '.repeat(indent + 1)}`
      indent += 1
      index += 1
      continue
    }
    if (ch === '}' || ch === ']') {
      indent = Math.max(0, indent - 1)
      out += `\n${'  '.repeat(indent)}${ch}`
      index += 1
      continue
    }
    if (ch === ',') {
      out += `,\n${'  '.repeat(indent)}`
      index += 1
      continue
    }
    if (ch === ':') {
      out += ': '
      index += 1
      continue
    }
    if (ch === '\n' || ch === '\r' || ch === ' ' || ch === '\t') {
      index += 1
      continue
    }
    let end = index
    while (end < length && !JSON_STRUCTURAL.has(source[end]) && !' \t\n\r'.includes(source[end])) end += 1
    out += source.slice(index, end)
    index = end
  }
  return out.replace(/[ \t]+\n/g, '\n').replace(/\n{2,}/g, '\n').trimEnd()
}
