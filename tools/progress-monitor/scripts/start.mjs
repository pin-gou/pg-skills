import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const args = process.argv.slice(2)
let projectRoot = ''
let stallThresholdMs = ''
const viteArgs = ['--host', '127.0.0.1', '--configLoader', 'runner']

for (let index = 0; index < args.length; index += 1) {
  const arg = args[index]
  if (arg === '--') {
    continue
  } else if (arg === '--project') {
    projectRoot = args[index + 1] || ''
    index += 1
  } else if (arg.startsWith('--project=')) {
    projectRoot = arg.slice('--project='.length)
  } else if (arg === '--stall-minutes') {
    stallThresholdMs = String(Number(args[index + 1]) * 60_000)
    index += 1
  } else if (arg.startsWith('--stall-minutes=')) {
    stallThresholdMs = String(Number(arg.slice('--stall-minutes='.length)) * 60_000)
  } else {
    viteArgs.push(arg)
  }
}

if (projectRoot) process.env.PG_PROJECT_ROOT = path.resolve(projectRoot)
if (stallThresholdMs) {
  const parsed = Number(stallThresholdMs)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    console.error('--stall-minutes must be a positive number')
    process.exit(2)
  }
  process.env.PG_MONITOR_STALL_MS = String(parsed)
}

const packageRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const viteBin = path.join(packageRoot, 'node_modules', 'vite', 'bin', 'vite.js')
const child = spawn(process.execPath, [viteBin, ...viteArgs], {
  cwd: packageRoot,
  env: process.env,
  stdio: 'inherit',
})

child.on('exit', code => process.exit(code ?? 1))
