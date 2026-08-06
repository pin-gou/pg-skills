import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { ChangeInfo, Manifest, Snapshot, TreeNode } from '@/types/pipeline'
import { api, HttpError } from '@/api/client'
import { buildTree } from '@/composables/buildTree'
import { countSnapshotPhases } from '@/shared/pipelineStatus'

export const usePipelineStore = defineStore('pipeline', () => {
  const changes = ref<ChangeInfo[]>([])
  const activeChanges = computed(() => changes.value.filter(change => change.isActive))
  const archivedChanges = computed(() => changes.value.filter(change => !change.isActive))

  const currentChange = ref<string | null>(null)
  const manifest = ref<Manifest | null>(null)
  const manifestRaw = ref('')
  const snapshot = ref<Snapshot | null>(null)
  const tree = ref<TreeNode[]>([])
  const loading = ref(false)
  const changesLoading = ref(false)
  const error = ref<string | null>(null)
  const refreshError = ref<string | null>(null)
  const lastUpdatedAt = ref<Date | null>(null)
  const refreshVersion = ref(0)

  const progress = computed(() => countSnapshotPhases(snapshot.value))

  async function optionalSnapshot(name: string): Promise<Snapshot | null> {
    try {
      return await api.getSnapshot(name)
    } catch (exception) {
      if (exception instanceof HttpError && exception.status === 404) return null
      throw exception
    }
  }

  async function loadChanges(silent = false): Promise<void> {
    if (!silent) changesLoading.value = true
    try {
      changes.value = await api.listChanges()
      error.value = null
    } catch (exception) {
      error.value = `Failed to load changes: ${String(exception)}`
    } finally {
      if (!silent) changesLoading.value = false
    }
  }

  async function loadChange(name: string): Promise<void> {
    currentChange.value = name
    loading.value = true
    error.value = null
    refreshError.value = null
    try {
      const [nextManifest, nextManifestRaw, nextSnapshot] = await Promise.all([
        api.getManifest(name),
        api.getManifestRaw(name),
        optionalSnapshot(name),
      ])
      manifest.value = nextManifest
      manifestRaw.value = nextManifestRaw
      snapshot.value = nextSnapshot
      tree.value = buildTree(nextManifest, nextSnapshot)
      lastUpdatedAt.value = new Date()
      refreshVersion.value += 1
    } catch (exception) {
      error.value = `Failed to load change ${name}: ${String(exception)}`
    } finally {
      loading.value = false
    }
  }

  async function refreshCurrent(): Promise<void> {
    if (!currentChange.value || !manifest.value) return
    try {
      const [nextSnapshot] = await Promise.all([
        optionalSnapshot(currentChange.value),
        loadChanges(true),
      ])
      snapshot.value = nextSnapshot
      tree.value = buildTree(manifest.value, nextSnapshot)
      refreshError.value = null
      lastUpdatedAt.value = new Date()
      refreshVersion.value += 1
    } catch (exception) {
      refreshError.value = `Refresh failed; showing last successful data: ${String(exception)}`
    }
  }

  function clearCurrent(): void {
    currentChange.value = null
    manifest.value = null
    manifestRaw.value = ''
    snapshot.value = null
    tree.value = []
    refreshError.value = null
  }

  return {
    changes,
    activeChanges,
    archivedChanges,
    currentChange,
    manifest,
    manifestRaw,
    snapshot,
    tree,
    loading,
    changesLoading,
    error,
    refreshError,
    lastUpdatedAt,
    refreshVersion,
    progress,
    loadChanges,
    loadChange,
    refreshCurrent,
    clearCurrent,
  }
})
