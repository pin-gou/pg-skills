import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'
import yaml from 'yaml'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function findRepoRoot(): string {
  const pgSkillsReal = fs.realpathSync(path.join(__dirname, '..', '..'))

  let dir = __dirname
  const visited = new Set<string>()
  while (true) {
    const resolved = fs.realpathSync(dir)
    if (visited.has(resolved)) break
    visited.add(resolved)
    const projectYaml = path.join(dir, '.pg', 'project.yaml')
    if (fs.existsSync(projectYaml)) {
      return dir
    }
    let entries: string[] = []
    try { entries = fs.readdirSync(dir) } catch { /* ignore */ }
    for (const entry of entries) {
      if (entry.startsWith('.') || entry === 'node_modules') continue
      const candidate = path.join(dir, entry)
      try {
        const candidateYaml = path.join(candidate, '.pg', 'project.yaml')
        if (fs.statSync(candidate).isDirectory() && fs.existsSync(candidateYaml)) {
          const skillsLink = path.join(candidate, '.pg', 'skills')
          try {
            if (fs.lstatSync(skillsLink).isSymbolicLink() && fs.realpathSync(skillsLink) === pgSkillsReal) {
              return candidate
            }
          } catch {
            return candidate
          }
        }
      } catch { /* ignore */ }
    }
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return process.cwd()
}

const repoRoot = findRepoRoot()

function readFileOrNull(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, 'utf-8')
  } catch {
    return null
  }
}

function readJsonOrNull(filePath: string): unknown | null {
  const text = readFileOrNull(filePath)
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

interface ChangeInfo {
  name: string
  isActive: boolean
  hasManifest: boolean
  hasSnapshot: boolean
  snapshotStatus: string | null
  mtime: string | null
}

function listChanges(): ChangeInfo[] {
  const changesDir = path.join(repoRoot, '.pg', 'changes')
  const archiveDir = path.join(changesDir, 'archive')
  const result: ChangeInfo[] = []

  if (fs.existsSync(changesDir)) {
    for (const entry of fs.readdirSync(changesDir, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name === 'archive') continue
      const changeRoot = path.join(changesDir, entry.name)
      const manifestPath = path.join(changeRoot, 'execution-manifest.yaml')
      const snapshotPath = path.join(changeRoot, '2-build', 'pipeline.snapshot.json')
      const hasManifest = fs.existsSync(manifestPath)
      const hasSnapshot = fs.existsSync(snapshotPath)
      let snapshotStatus: string | null = null
      if (hasSnapshot) {
        const snap = readJsonOrNull(snapshotPath) as Record<string, unknown> | null
        snapshotStatus = snap?.status as string ?? null
      }
      let mtime: string | null = null
      try {
        const stat = fs.statSync(manifestPath)
        mtime = stat.mtime.toISOString()
      } catch { /* ignore */ }
      result.push({ name: entry.name, isActive: true, hasManifest, hasSnapshot, snapshotStatus, mtime })
    }
  }

  if (fs.existsSync(archiveDir)) {
    for (const entry of fs.readdirSync(archiveDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const changeRoot = path.join(archiveDir, entry.name)
      const manifestPath = path.join(changeRoot, 'execution-manifest.yaml')
      const snapshotPath = path.join(changeRoot, '2-build', 'pipeline.snapshot.json')
      const hasManifest = fs.existsSync(manifestPath)
      const hasSnapshot = fs.existsSync(snapshotPath)
      let snapshotStatus: string | null = null
      if (hasSnapshot) {
        const snap = readJsonOrNull(snapshotPath) as Record<string, unknown> | null
        snapshotStatus = snap?.status as string ?? null
      }
      let mtime: string | null = null
      try {
        const stat = fs.statSync(manifestPath)
        mtime = stat.mtime.toISOString()
      } catch { /* ignore */ }
      result.push({ name: entry.name, isActive: false, hasManifest, hasSnapshot, snapshotStatus, mtime })
    }
  }

  result.sort((a, b) => {
    if (a.isActive !== b.isActive) return a.isActive ? -1 : 1
    return (b.mtime ?? '').localeCompare(a.mtime ?? '')
  })

  return result
}

function listArtifactsForPhase(changeRoot: string, track: string, phase: string): string[] {
  const buildDir = path.join(changeRoot, '2-build')
  if (!fs.existsSync(buildDir)) return []
  const files = fs.readdirSync(buildDir)
  const pattern = track.replace(/^.*\./, '') + '-' + phase
  return files
    .filter(f => f.includes(pattern) && !f.endsWith('.json') && !f.startsWith('pipeline.'))
    .sort()
}

function readEvents(changeRoot: string, page: number, size: number): { events: unknown[]; total: number } {
  const eventsPath = path.join(changeRoot, '2-build', 'pipeline.events')
  const text = readFileOrNull(eventsPath)
  if (!text) return { events: [], total: 0 }
  const lines = text.trim().split('\n').filter(Boolean)
  const parsed = lines.map((l, i) => {
    try {
      const obj = JSON.parse(l)
      obj._line = i + 1
      return obj
    } catch {
      return { type: 'parse_error', _line: i + 1, raw: l.slice(0, 200) }
    }
  })
  const total = parsed.length
  const start = (page - 1) * size
  const end = Math.min(start + size, total)
  const events = parsed.slice(start, end).reverse()
  return { events, total }
}

export default defineConfig(({ command }) => ({
  plugins: [
    {
      name: 'pg-progress-monitor-server',
      enforce: 'pre',
      configureServer(server) {
        server.middlewares.use(async (req, res, next) => {
          const url = req.url || ''

          // CORS
          res.setHeader('Access-Control-Allow-Origin', '*')
          res.setHeader('Cache-Control', 'no-cache')

          // List changes
          if (url === '/__pg/changes') {
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify(listChanges()))
            return
          }

          // Manifest
          const manifestMatch = url.match(/^\/__pg\/manifest\/(.+)$/)
          if (manifestMatch) {
            const change = manifestMatch[1]
            const filePath = path.join(repoRoot, '.pg', 'changes', change, 'execution-manifest.yaml')
            const archivePath = path.join(repoRoot, '.pg', 'changes', 'archive', change, 'execution-manifest.yaml')
            let content = readFileOrNull(filePath) || readFileOrNull(archivePath)
            if (!content) {
              res.statusCode = 404
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: 'manifest not found' }))
              return
            }
            try {
              const parsed = yaml.parse(content)
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify(parsed))
            } catch {
              res.setHeader('Content-Type', 'text/plain')
              res.end(content)
            }
            return
          }

          // Snapshot
          const snapMatch = url.match(/^\/__pg\/snapshot\/(.+)$/)
          if (snapMatch) {
            const change = snapMatch[1]
            const filePath = path.join(repoRoot, '.pg', 'changes', change, '2-build', 'pipeline.snapshot.json')
            const archivePath = path.join(repoRoot, '.pg', 'changes', 'archive', change, '2-build', 'pipeline.snapshot.json')
            const content = readFileOrNull(filePath) || readFileOrNull(archivePath)
            if (!content) {
              res.statusCode = 404
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: 'snapshot not found' }))
              return
            }
            res.setHeader('Content-Type', 'application/json')
            res.end(content)
            return
          }

          // Events
          const eventsMatch = url.match(/^\/__pg\/events\/(.+?)(?:\?|$)/)
          if (eventsMatch) {
            const change = eventsMatch[1]
            const params = new URL(url, 'http://localhost').searchParams
            const page = parseInt(params.get('page') || '1', 10)
            const size = parseInt(params.get('size') || '50', 10)
            const changeRoot = path.join(repoRoot, '.pg', 'changes', change)
            const archiveRoot = path.join(repoRoot, '.pg', 'changes', 'archive', change)
            const root = fs.existsSync(changeRoot) ? changeRoot : archiveRoot
            const result = readEvents(root, Math.max(1, page), Math.max(1, Math.min(200, size)))
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify(result))
            return
          }

          // Artifact content
          const artifactMatch = url.match(/^\/__pg\/artifact\/(.+?)\?path=(.+)$/)
          if (artifactMatch) {
            const change = artifactMatch[1]
            const relPath = decodeURIComponent(artifactMatch[2])
            const changeRoot = path.join(repoRoot, '.pg', 'changes', change)
            const archiveRoot = path.join(repoRoot, '.pg', 'changes', 'archive', change)
            const base = fs.existsSync(changeRoot) ? changeRoot : archiveRoot
            const filePath = path.join(base, '2-build', relPath)
            const content = readFileOrNull(filePath)
            if (!content) {
              res.statusCode = 404
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: 'artifact not found' }))
              return
            }
            const ext = path.extname(filePath)
            if (ext === '.json') {
              res.setHeader('Content-Type', 'application/json')
            } else {
              res.setHeader('Content-Type', 'text/plain; charset=utf-8')
            }
            res.end(content)
            return
          }

          // Artifacts list for phase
          const artifactsMatch = url.match(/^\/__pg\/artifacts\/(.+?)\?track=(.+?)&phase=(.+)$/)
          if (artifactsMatch) {
            const change = artifactsMatch[1]
            const track = decodeURIComponent(artifactsMatch[2])
            const phase = decodeURIComponent(artifactsMatch[3])
            const changeRoot = path.join(repoRoot, '.pg', 'changes', change)
            const archiveRoot = path.join(repoRoot, '.pg', 'changes', 'archive', change)
            const root = fs.existsSync(changeRoot) ? changeRoot : archiveRoot
            const files = listArtifactsForPhase(root, track, phase)
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify(files))
            return
          }

          // Serve .pg/ files directly (for YAML viewer etc.)
          if (url.startsWith('/.pg/')) {
            const filePath = path.join(repoRoot, url)
            if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
              const content = fs.readFileSync(filePath, 'utf-8')
              const ext = path.extname(filePath)
              const mime: Record<string, string> = {
                '.yaml': 'text/yaml', '.yml': 'text/yaml',
                '.json': 'application/json', '.html': 'text/html',
                '.js': 'application/javascript', '.css': 'text/css',
                '.ts': 'application/typescript', '.sh': 'text/x-shellscript',
                '.md': 'text/markdown',
              }
              res.setHeader('Content-Type', mime[ext] || 'application/octet-stream')
              res.end(content)
              return
            }
          }

          next()
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
    port: 9323,
    strictPort: true,
  },
  base: command === 'build' ? '/.pg/skills/tools/progress-monitor/dist/' : '/',
  build: {
    outDir: path.resolve(__dirname, 'dist'),
  },
}))