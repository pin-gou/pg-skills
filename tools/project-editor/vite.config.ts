import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function findRepoRoot(startDir: string): string {
  let dir = startDir
  while (true) {
    if (fs.existsSync(path.join(dir, '.pg', 'project.yaml'))) return dir
    const entries = fs.readdirSync(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name === 'node_modules') continue
      if (fs.existsSync(path.join(dir, entry.name, '.pg', 'project.yaml'))) {
        return path.join(dir, entry.name)
      }
    }
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return startDir
}

const repoRoot = findRepoRoot(__dirname)

export default defineConfig(({ command }) => ({
  plugins: [
    {
      name: 'serve-repo-root-files',
      enforce: 'pre',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const url = req.url || ''
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
              }
              res.setHeader('Content-Type', mime[ext] || 'application/octet-stream')
              res.setHeader('Access-Control-Allow-Origin', '*')
              res.setHeader('Cache-Control', 'no-cache')
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
    port: 3008,
    strictPort: false,
  },
  base: command === 'build' ? '/.pg/skills/tools/project-editor/dist/' : '/',
  build: {
    outDir: path.resolve(__dirname, 'dist'),
  },
}))
