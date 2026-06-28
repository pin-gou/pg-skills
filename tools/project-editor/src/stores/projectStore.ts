import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { parseYaml, dumpYaml } from '@/utils/yaml'
import { diffCount, type DiffEntry } from '@/utils/diff'
import { loadSchema, compileValidator, validateProject, type ValidationError } from '@/schema/loader'
import { applyHash, parseHash, type ParsedHash } from '@/utils/hash'

export type ViewMode = 'dashboard' | 'form' | 'canvas' | 'diff'

export type SelectionState = {
  module?: string
  env?: string
  track?: string
  stage?: string
  field?: string
}

export const useProjectStore = defineStore('project', () => {
  const data = ref<Record<string, unknown>>({})
  const rawOriginal = ref<string>('')
  const rawCurrent = ref<string>('')
  const errors = ref<ValidationError[]>([])
  const schemaLoaded = ref(false)
  const dirty = ref(false)
  const loading = ref(true)
  const errorMessage = ref<string>('')

  const view = ref<ViewMode>('dashboard')
  const selection = ref<SelectionState>({})

  const isValid = computed(() => errors.value.length === 0)
  const dirtyCount = computed(() => diffCount(rawOriginal.value, data.value))

  const diffEntries = computed<DiffEntry[]>(() => {
    return []
  })

  function syncFromHash() {
    const parsed = parseHash(window.location.hash, window.location.search)
    view.value = parsed.view
    selection.value = parsed.selection
  }

  function syncToHash() {
    applyHash({ view: view.value, selection: selection.value })
  }

  function setView(v: ViewMode) {
    view.value = v
    syncToHash()
  }

  function selectModule(name?: string) {
    selection.value = { ...selection.value, module: name }
    syncToHash()
  }

  function selectEnv(name?: string) {
    selection.value = { ...selection.value, env: name }
    syncToHash()
  }

  function selectTrack(name?: string) {
    selection.value = { ...selection.value, track: name }
    syncToHash()
  }

  function selectStage(name?: string) {
    selection.value = { ...selection.value, stage: name }
    syncToHash()
  }

  function selectField(path?: string) {
    selection.value = { ...selection.value, field: path }
    syncToHash()
  }

  async function load() {
    loading.value = true
    errorMessage.value = ''
    try {
      const r = await fetch('/.pg/project.yaml')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const text = await r.text()
      rawOriginal.value = text
      rawCurrent.value = text
      const parsed = parseYaml(text)
      if (parsed) data.value = parsed as Record<string, unknown>

      const schemaObj = await loadSchema()
      if (schemaObj) {
        compileValidator(schemaObj)
        schemaLoaded.value = true
        runValidation()
      }
      syncFromHash()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      errorMessage.value = `加载失败: ${msg}`
    } finally {
      loading.value = false
    }
  }

  function runValidation() {
    if (!schemaLoaded.value) return
    errors.value = validateProject(data.value)
  }

  function getAt(path: (string | number)[]): unknown {
    let cur: unknown = data.value
    for (const k of path) {
      if (cur === null || cur === undefined) return undefined
      if (typeof cur !== 'object') return undefined
      cur = (cur as Record<string | number, unknown>)[k]
    }
    return cur
  }

  function setAt(path: (string | number)[], value: unknown): void {
    if (path.length === 0) {
      if (value === null || value === undefined) {
        data.value = {}
      } else {
        data.value = value as Record<string, unknown>
      }
      dirty.value = true
      runValidation()
      return
    }
    let cur = data.value as Record<string | number, unknown>
    for (let i = 0; i < path.length - 1; i++) {
      const k = path[i]
      const next = cur[k]
      if (next === undefined || next === null || typeof next !== 'object') {
        const nextIsIndex = typeof path[i + 1] === 'number'
        cur[k] = nextIsIndex ? [] : {}
      }
      cur = cur[k] as Record<string | number, unknown>
    }
    const lastKey = path[path.length - 1]
    if (value === undefined) {
      if (Array.isArray(cur) && typeof lastKey === 'number') {
        cur.splice(lastKey, 1)
      } else {
        delete cur[lastKey]
      }
    } else {
      cur[lastKey] = value
    }
    dirty.value = true
    runValidation()
  }

  function deleteAt(path: (string | number)[]): void {
    setAt(path, undefined)
  }

  function appendAt(parentPath: (string | number)[], value: unknown): number {
    const arr = getAt(parentPath)
    if (!Array.isArray(arr)) {
      setAt(parentPath, [])
    }
    const list = getAt(parentPath) as unknown[]
    list.push(value)
    setAt(parentPath, list)
    return list.length - 1
  }

  async function save(): Promise<boolean> {
    const yaml = dumpYaml(data.value, rawOriginal.value)
    const ok = await writeFile('.pg/project.yaml', yaml)
    if (ok) {
      rawOriginal.value = yaml
      rawCurrent.value = yaml
      dirty.value = false
    }
    return ok
  }

  async function writeFile(path: string, content: string): Promise<boolean> {
    const w = window as unknown as {
      showSaveFilePicker?: (opts: unknown) => Promise<FileSystemFileHandle>
    }
    if (w.showSaveFilePicker) {
      try {
        const handle = await w.showSaveFilePicker({
          suggestedName: 'project.yaml',
          types: [{ description: 'YAML', accept: { 'text/yaml': ['.yaml', '.yml'] } }],
        })
        const writable = await handle.createWritable()
        await writable.write(content)
        await writable.close()
        return true
      } catch (e) {
        if (e instanceof Error && e.name === 'AbortError') return false
      }
    }
    const blob = new Blob([content], { type: 'text/yaml' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'project.yaml'
    a.click()
    URL.revokeObjectURL(a.href)
    return true
  }

  function reload() {
    return load()
  }

  function getSection<T = unknown>(section: string): T {
    return (data.value[section] ?? {}) as T
  }

  watch(view, syncToHash)
  watch(selection, syncToHash, { deep: true })

  return {
    data, rawOriginal, rawCurrent, errors, schemaLoaded, dirty, loading, errorMessage,
    view, selection,
    isValid, dirtyCount,
    load, runValidation, reload,
    getAt, setAt, deleteAt, appendAt,
    save,
    setView, selectModule, selectEnv, selectTrack, selectStage, selectField,
    getSection,
  }
})