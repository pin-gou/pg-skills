import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'
import yaml from 'yaml'
import { filterEvents, paginateNewest } from './server/events'
import { listChanges, parseJsonFile, readFileOrNull } from './server/change-info'
import { findProjectRoot, isSafeSegment, resolvePathInside } from './server/path-utils'
import { enrichSnapshotPhaseTelemetry } from './server/phase-telemetry'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
let repoRoot = process.cwd()
let changesRoot = path.join(repoRoot, '.pg', 'changes')
const DEFAULT_STALL_THRESHOLD_MS = 5 * 60_000

function stallThresholdMs(): number {
  const configured = Number(process.env.PG_MONITOR_STALL_MS)
  return Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_STALL_THRESHOLD_MS
}

function decodeSafeSegment(encoded: string): string {
  const value = decodeURIComponent(encoded)
  if (!isSafeSegment(value)) throw new Error('Invalid change name')
  return value
}

function resolveChangeRoot(change: string): string | null {
  const active = path.join(changesRoot, change)
  if (fs.existsSync(active) && fs.statSync(active).isDirectory()) return active
  const archived = path.join(changesRoot, 'archive', change)
  if (fs.existsSync(archived) && fs.statSync(archived).isDirectory()) return archived
  return null
}

function listArtifactsForPhase(changeRoot: string, track: string, phase: string): string[] {
  const buildDir = path.join(changeRoot, '2-build')
  if (!fs.existsSync(buildDir)) return []
  const bareTrack = track.replace(/^.*\./, '')
  const phasePattern = `${bareTrack}-${phase}`
  const fixPattern = `${bareTrack}-fix`
  return fs.readdirSync(buildDir, { withFileTypes: true })
    .filter(entry => {
      if (!entry.isFile()) return false
      if (entry.name.startsWith('pipeline.')) return false
      return entry.name.includes(phasePattern) || entry.name.includes(fixPattern)
    })
    .map(entry => entry.name)
    .sort()
}

function readEvents(
  changeRoot: string,
  page: number,
  size: number,
  search = '',
  failuresOnly = false,
): { events: unknown[]; total: number } {
  const text = readFileOrNull(path.join(changeRoot, '2-build', 'pipeline.events'))
  if (!text) return { events: [], total: 0 }
  const parsed = text.trim().split(/\r?\n/).filter(Boolean).map((line, index) => {
    try {
      return { ...JSON.parse(line), _line: index + 1 }
    } catch (error) {
      return { type: 'parse_error', _line: index + 1, raw: line.slice(0, 500), error: String(error) }
    }
  })
  const filtered = filterEvents(parsed, search, failuresOnly)
  return { events: paginateNewest(filtered, page, size), total: filtered.length }
}

function readAllEvents(changeRoot: string): unknown[] {
  const text = readFileOrNull(path.join(changeRoot, '2-build', 'pipeline.events'))
  if (!text) return []
  return text.trim().split(/\r?\n/).filter(Boolean).flatMap(line => {
    try {
      return [JSON.parse(line)]
    } catch {
      return []
    }
  })
}

function sendJson(res: import('node:http').ServerResponse, status: number, value: unknown): void {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(value))
}

function sendText(res: import('node:http').ServerResponse, status: number, value: string): void {
  res.statusCode = status
  res.setHeader('Content-Type', 'text/plain; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(value)
}

export default defineConfig(({ command }) => {
  if (command === 'serve') {
    repoRoot = findProjectRoot(process.cwd(), process.env.PG_PROJECT_ROOT)
    changesRoot = path.join(repoRoot, '.pg', 'changes')
  }

  return {
  plugins: [
    {
      name: 'pg-progress-monitor-server',
      enforce: 'pre',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const requestUrl = new URL(req.url || '/', 'http://127.0.0.1')
          const pathname = requestUrl.pathname

          try {
            if (pathname === '/__pg/changes') {
              sendJson(res, 200, listChanges(changesRoot, stallThresholdMs()))
              return
            }

            const manifestRawMatch = pathname.match(/^\/__pg\/manifest-raw\/([^/]+)$/)
            if (manifestRawMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(manifestRawMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const content = readFileOrNull(path.join(root, 'execution-manifest.yaml'))
              if (content === null) return sendJson(res, 404, { error: 'manifest not found' })
              sendText(res, 200, content)
              return
            }

            const manifestMatch = pathname.match(/^\/__pg\/manifest\/([^/]+)$/)
            if (manifestMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(manifestMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const content = readFileOrNull(path.join(root, 'execution-manifest.yaml'))
              if (content === null) return sendJson(res, 404, { error: 'manifest not found' })
              try {
                sendJson(res, 200, yaml.parse(content))
              } catch (error) {
                sendJson(res, 422, { error: 'manifest parse failed', detail: String(error) })
              }
              return
            }

            const snapshotMatch = pathname.match(/^\/__pg\/snapshot\/([^/]+)$/)
            if (snapshotMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(snapshotMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const filePath = path.join(root, '2-build', 'pipeline.snapshot.json')
              const parsed = parseJsonFile(filePath)
              if (!fs.existsSync(filePath)) return sendJson(res, 404, { error: 'snapshot not found' })
              if (parsed.error) return sendJson(res, 422, { error: 'snapshot parse failed', detail: parsed.error })
              sendJson(res, 200, enrichSnapshotPhaseTelemetry(parsed.value, readAllEvents(root)))
              return
            }

            const eventsMatch = pathname.match(/^\/__pg\/events\/([^/]+)$/)
            if (eventsMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(eventsMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const page = Math.max(1, Number.parseInt(requestUrl.searchParams.get('page') || '1', 10) || 1)
              const size = Math.max(1, Math.min(200, Number.parseInt(requestUrl.searchParams.get('size') || '50', 10) || 50))
              const search = requestUrl.searchParams.get('q') || ''
              const failuresOnly = requestUrl.searchParams.get('failures') === 'true'
              sendJson(res, 200, readEvents(root, page, size, search, failuresOnly))
              return
            }

            const artifactMatch = pathname.match(/^\/__pg\/artifact\/([^/]+)$/)
            if (artifactMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(artifactMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const requested = requestUrl.searchParams.get('path') || ''
              let filePath: string
              try {
                filePath = resolvePathInside(path.join(root, '2-build'), requested)
              } catch (error) {
                return sendJson(res, 403, { error: 'artifact path rejected', detail: String(error) })
              }
              const content = readFileOrNull(filePath)
              if (content === null || !fs.statSync(filePath).isFile()) return sendJson(res, 404, { error: 'artifact not found' })
              const extension = path.extname(filePath).toLowerCase()
              res.statusCode = 200
              res.setHeader('Cache-Control', 'no-store')
              res.setHeader('Content-Type', extension === '.json' ? 'application/json; charset=utf-8' : 'text/plain; charset=utf-8')
              res.end(content)
              return
            }

            const artifactsMatch = pathname.match(/^\/__pg\/artifacts\/([^/]+)$/)
            if (artifactsMatch) {
              const root = resolveChangeRoot(decodeSafeSegment(artifactsMatch[1]))
              if (!root) return sendJson(res, 404, { error: 'change not found' })
              const track = requestUrl.searchParams.get('track') || ''
              const phase = requestUrl.searchParams.get('phase') || ''
              if (!track || !phase) return sendJson(res, 400, { error: 'track and phase are required' })
              sendJson(res, 200, listArtifactsForPhase(root, track, phase))
              return
            }

            next()
          } catch (error) {
            sendJson(res, 400, { error: 'invalid monitor request', detail: String(error) })
          }
        })
      },
    },
    vue(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 9323,
    strictPort: true,
  },
  base: command === 'build' ? '/.pg/skills/tools/progress-monitor/dist/' : '/',
  build: {
    outDir: path.resolve(__dirname, 'dist'),
  },
  }
})
