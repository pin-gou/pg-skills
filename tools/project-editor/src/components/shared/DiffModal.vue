<template>
  <div v-if="open" class="diff-modal-bg" @click.self="emit('cancel')">
    <div class="diff-modal">
      <header class="modal-header">
        <h3>🔍 对比</h3>
        <button class="btn-icon" @click="emit('cancel')">×</button>
      </header>
      <div class="modal-body">
        <div class="modal-intro">即将修改 <code>.pg/project.yaml</code></div>
        <div v-if="entries.length === 0" class="empty">无差异</div>
        <div v-else class="diff-list">
          <div v-for="(e, i) in entries" :key="i" class="diff-entry" :class="`kind-${e.kind}`">
            <span class="kind-tag">{{ e.kind }}</span>
            <span class="diff-path">{{ e.path || '(root)' }}</span>
            <div v-if="e.kind === '+' || e.kind === '~'" class="diff-line">
              <span class="arrow">+</span>
              <span class="value">{{ formatValue(e.after) }}</span>
            </div>
            <div v-if="e.kind === '-' || e.kind === '~'" class="diff-line">
              <span class="arrow">−</span>
              <span class="value">{{ formatValue(e.before) }}</span>
            </div>
          </div>
        </div>
        <div class="validation-status">
          <template v-if="store.isValid">
            <span class="ok">⚠ schema 校验: 通过 (0 errors)</span>
          </template>
          <template v-else>
            <span class="err">⚠ schema 校验: {{ store.errors.length }} 个错误</span>
          </template>
        </div>
      </div>
      <footer class="modal-footer">
        <button class="btn" @click="emit('cancel')">Cancel</button>
        <button
          class="btn primary"
          :disabled="!store.isValid"
          @click="emit('confirm')"
        >Confirm Save</button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import { parseYaml } from '@/utils/yaml'
import { diffFields } from '@/utils/diff'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ cancel: []; confirm: [] }>()

const store = useProjectStore()

const originalParsed = ref<Record<string, unknown>>({})

watch(() => store.rawOriginal, (text) => {
  originalParsed.value = (parseYaml(text) as Record<string, unknown>) || {}
}, { immediate: true })

const entries = computed(() => {
  return diffFields(originalParsed.value, store.data)
})

function formatValue(v: unknown): string {
  if (v === undefined) return ''
  if (v === null) return 'null'
  if (typeof v === 'string') return v.length > 80 ? v.slice(0, 80) + '…' : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}
</script>

<style scoped>
.diff-modal-bg {
  position: fixed; inset: 0; background: rgba(0,0,0,.6);
  display: flex; align-items: center; justify-content: center;
  z-index: 200;
}
.diff-modal {
  width: 720px; max-width: 95vw; max-height: 80vh;
  background: #252526; border: 1px solid #3c3c3c;
  border-radius: 8px;
  display: flex; flex-direction: column;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border-bottom: 1px solid #3c3c3c;
}
.modal-header h3 { margin: 0; font-size: 15px; color: #e0e0e0; }
.modal-body { flex: 1; overflow-y: auto; padding: 16px; }
.modal-intro { font-size: 12px; color: #aaa; margin-bottom: 12px; }
.modal-intro code { color: #4fc3f7; }
.empty { font-size: 13px; color: #888; padding: 24px; text-align: center; }
.diff-list { display: flex; flex-direction: column; gap: 8px; }
.diff-entry {
  background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px;
  padding: 8px 12px; font-family: Consolas, monospace; font-size: 12px;
}
.diff-entry.kind-add { border-left: 3px solid #2e7d32; }
.diff-entry.kind-del { border-left: 3px solid #b71c1c; }
.diff-entry.kind-mod { border-left: 3px solid #f57c00; }
.kind-tag {
  display: inline-block; width: 14px; font-weight: bold; margin-right: 8px;
  text-align: center;
}
.kind-add .kind-tag { color: #81c784; }
.kind-del .kind-tag { color: #ef5350; }
.kind-mod .kind-tag { color: #ffb74d; }
.diff-path { color: #4fc3f7; font-weight: 600; }
.diff-line { margin-left: 22px; margin-top: 2px; }
.diff-line .arrow {
  display: inline-block; width: 16px; color: #aaa; font-weight: bold;
}
.kind-add .arrow { color: #81c784; }
.kind-del .arrow { color: #ef5350; }
.kind-mod .arrow { color: #ffb74d; }
.diff-line .value { color: #d4d4d4; word-break: break-all; }
.validation-status {
  margin-top: 16px; padding-top: 12px; border-top: 1px solid #3c3c3c;
  font-size: 12px;
}
.validation-status .ok { color: #81c784; }
.validation-status .err { color: #ef5350; }
.modal-footer {
  padding: 12px 16px; border-top: 1px solid #3c3c3c;
  display: flex; justify-content: flex-end; gap: 8px;
}
.btn-icon {
  background: transparent; border: none; color: #aaa; font-size: 18px;
  width: 24px; height: 24px;
}
.btn-icon:hover { color: #fff; }
.btn {
  background: #3c3c3c; border: 1px solid #555; color: #d4d4d4;
  padding: 4px 14px; border-radius: 4px; font-size: 12px;
}
.btn:hover { background: #505050; }
.btn.primary { background: #1565c0; border-color: #1976d2; }
.btn.primary:hover { background: #1976d2; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
</style>