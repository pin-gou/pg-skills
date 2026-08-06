import fs from 'node:fs'
import path from 'node:path'

export function isSafeSegment(value: string): boolean {
  return value.length > 0 && value !== '.' && value !== '..' && path.basename(value) === value
}

export function resolvePathInside(baseDir: string, requestedPath: string): string {
  if (!requestedPath || path.isAbsolute(requestedPath) || requestedPath.includes('\0')) {
    throw new Error('invalid path')
  }

  const base = path.resolve(baseDir)
  const candidate = path.resolve(base, requestedPath)
  if (candidate !== base && !candidate.startsWith(`${base}${path.sep}`)) {
    throw new Error('path escapes allowed directory')
  }

  if (fs.existsSync(candidate)) {
    const realBase = fs.realpathSync(base)
    const realCandidate = fs.realpathSync(candidate)
    if (realCandidate !== realBase && !realCandidate.startsWith(`${realBase}${path.sep}`)) {
      throw new Error('path resolves outside allowed directory')
    }
  }

  return candidate
}

export function findProjectRoot(startDir: string, explicitRoot?: string): string {
  if (explicitRoot) {
    const root = path.resolve(explicitRoot)
    if (!fs.existsSync(path.join(root, '.pg', 'project.yaml'))) {
      throw new Error(`No .pg/project.yaml found under explicit project root: ${root}`)
    }
    return root
  }

  let dir = path.resolve(startDir)
  while (true) {
    if (fs.existsSync(path.join(dir, '.pg', 'project.yaml'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }

  throw new Error('Unable to locate .pg/project.yaml. Start from a pg-skills project or pass --project <path>.')
}
