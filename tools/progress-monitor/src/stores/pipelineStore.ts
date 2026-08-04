import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ChangeInfo, Manifest, Snapshot, TreeNode } from '@/types/pipeline'
import { api } from '@/api/client'
import { buildTree } from '@/composables/buildTree'

export const usePipelineStore = defineStore('pipeline', () => {
  const changes = ref<ChangeInfo[]>([])
  const activeChanges = computed(() => changes.value.filter(c => c.isActive))
  const archivedChanges = computed(() => changes.value.filter(c => !c.isActive))

  const currentChange = ref<string | null>(null)
  const manifest = ref<Manifest | null>(null)
  const snapshot = ref<Snapshot | null>(null)
  const tree = ref<TreeNode[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function loadChanges() {
    try {
      changes.value = await api.listChanges()
    } catch (e) {
      error.value = `加载变更列表失败: ${e}`
    }
  }

  async function loadChange(name: string) {
    currentChange.value = name
    loading.value = true
    error.value = null
    try {
      const [m, s] = await Promise.all([
        api.getManifest(name),
        api.getSnapshot(name).catch(() => null),
      ])
      manifest.value = m
      snapshot.value = s
      tree.value = buildTree(m, s)
    } catch (e) {
      error.value = `加载变更 ${name} 失败: ${e}`
    } finally {
      loading.value = false
    }
  }

  async function refreshSnapshot() {
    if (!currentChange.value) return
    try {
      snapshot.value = await api.getSnapshot(currentChange.value)
      if (manifest.value) {
        tree.value = buildTree(manifest.value, snapshot.value)
      }
    } catch {
      // silent
    }
  }

  return {
    changes, activeChanges, archivedChanges,
    currentChange, manifest, snapshot, tree,
    loading, error,
    loadChanges, loadChange, refreshSnapshot,
  }
})