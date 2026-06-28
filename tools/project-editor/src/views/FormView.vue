<template>
  <div class="form-view">
    <aside class="section-nav">
      <div
        v-for="g in groups"
        :key="g.id"
        class="nav-group"
      >
        <div class="nav-group-title">{{ g.label }}</div>
        <button
          v-for="s in g.sections"
          :key="s.id"
          class="nav-item"
          :class="{ active: activeSection === s.id }"
          @click="activeSection = s.id"
        >
          <span class="nav-icon">{{ s.icon }}</span>
          <span class="nav-name">{{ s.label }}</span>
          <span v-if="countFor(s.id)" class="nav-count">{{ countFor(s.id) }}</span>
        </button>
      </div>
    </aside>

    <div class="section-detail">
      <TopSchemaSection v-if="activeSection === 'top'" />
      <RuleListSection
        v-else-if="activeSection === 'build_rules'"
        title="🔨 build_rules — 构建期注入提示"
        section-key="build_rules"
        item-label="rule"
        hint="target_agent 必须是 pg-build/* agent, position: prepend/append"
      />
      <RuleListSection
        v-else-if="activeSection === 'proposal_rules'"
        title="📋 proposal_rules — proposal 章节模板"
        section-key="proposal_rules"
        item-label="rule"
        hint="注入到 proposal.md 的固定章节, 支持 after_section 锚点"
      />
      <component :is="resolvedSection" v-else />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useProjectStore } from '@/stores/projectStore'
import TopSchemaSection from '@/components/sections/TopSchemaSection.vue'
import ModulesSection from '@/components/sections/ModulesSection.vue'
import EnvironmentsSection from '@/components/sections/EnvironmentsSection.vue'
import TracksSection from '@/components/sections/TracksSection.vue'
import StagesSection from '@/components/sections/StagesSection.vue'
import FixIssueSection from '@/components/sections/FixIssueSection.vue'
import RegressionSection from '@/components/sections/RegressionSection.vue'
import RulesSection from '@/components/sections/RulesSection.vue'
import RuleListSection from '@/components/sections/RuleListSection.vue'
import VerifyMergeSection from '@/components/sections/VerifyMergeSection.vue'
import FlywaySection from '@/components/sections/FlywaySection.vue'
import GitSection from '@/components/sections/GitSection.vue'
import TestStrategySection from '@/components/sections/TestStrategySection.vue'
import CodingStandardsSection from '@/components/sections/CodingStandardsSection.vue'

const store = useProjectStore()
const activeSection = ref<string>('top')

const groups = [
  {
    id: 'core', label: '核心 4 段 (SSOT)', sections: [
      { id: 'top', label: '顶级参数', icon: '⚙' },
      { id: 'modules', label: 'modules', icon: '📦' },
      { id: 'environments', label: 'environments', icon: '🌐' },
      { id: 'tracks', label: 'tracks', icon: '🛤' },
      { id: 'stages', label: 'stages', icon: '⏱' },
    ],
  },
  {
    id: 'skills', label: 'SKILL 专用', sections: [
      { id: 'fix_issue', label: 'fix_issue', icon: '🔧' },
      { id: 'regression', label: 'regression', icon: '📊' },
    ],
  },
  {
    id: 'tools', label: '辅助工具', sections: [
      { id: 'verify_merge', label: 'verify_merge', icon: '✅' },
      { id: 'flyway', label: 'flyway', icon: '🗄' },
      { id: 'git', label: 'git', icon: '🌿' },
    ],
  },
  {
    id: 'ext', label: '扩展', sections: [
      { id: 'rules', label: 'rules', icon: '📐' },
      { id: 'build_rules', label: 'build_rules', icon: '🔨' },
      { id: 'proposal_rules', label: 'proposal_rules', icon: '📋' },
      { id: 'test_strategy', label: 'test_strategy', icon: '🧪' },
      { id: 'coding_standards', label: 'coding_standards', icon: '📏' },
    ],
  },
]

const counts = computed<Record<string, number>>(() => {
  const data = store.data
  return {
    modules: Object.keys(data.modules || {}).length,
    environments: Object.keys(data.environments || {}).length,
    tracks: Object.keys(data.tracks || {}).length,
    stages: ((data.stages as unknown[]) || []).length,
    regression: Object.keys(((data.regression as any)?.suite) || {}).length,
    rules: Object.keys(data.rules || {}).length,
    build_rules: ((data.build_rules as unknown[]) || []).length,
    proposal_rules: ((data.proposal_rules as unknown[]) || []).length,
  }
})

function countFor(id: string): number {
  return counts.value[id] || 0
}

const resolvedSection = computed(() => {
  switch (activeSection.value) {
    case 'modules': return ModulesSection
    case 'environments': return EnvironmentsSection
    case 'tracks': return TracksSection
    case 'stages': return StagesSection
    case 'fix_issue': return FixIssueSection
    case 'regression': return RegressionSection
    case 'rules': return RulesSection
    case 'build_rules': return RuleListSection
    case 'proposal_rules': return RuleListSection
    case 'verify_merge': return VerifyMergeSection
    case 'flyway': return FlywaySection
    case 'git': return GitSection
    case 'test_strategy': return TestStrategySection
    case 'coding_standards': return CodingStandardsSection
    default: return ModulesSection
  }
})
</script>

<style scoped>
.form-view {
  display: flex;
  height: 100%;
  overflow: hidden;
}
.section-nav {
  width: 220px;
  flex-shrink: 0;
  background: #252526;
  border-right: 1px solid #3c3c3c;
  overflow-y: auto;
  padding: 12px 8px;
}
.nav-group { margin-bottom: 12px; }
.nav-group-title {
  font-size: 10px; font-weight: 600; color: #888;
  padding: 4px 8px; text-transform: uppercase; letter-spacing: 0.05em;
}
.nav-item {
  display: flex; align-items: center; gap: 6px;
  width: 100%; background: transparent; border: none;
  padding: 5px 10px; border-radius: 4px; cursor: pointer;
  font-size: 12px; color: #aaa; text-align: left;
  margin-bottom: 1px;
}
.nav-item:hover { background: #2d2d2d; color: #d4d4d4; }
.nav-item.active { background: #1565c0; color: #fff; }
.nav-icon { width: 16px; text-align: center; }
.nav-name { flex: 1; font-family: Consolas, monospace; }
.nav-count {
  background: rgba(255,255,255,0.15); font-size: 10px;
  padding: 1px 6px; border-radius: 8px; min-width: 18px; text-align: center;
}
.section-detail {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-width: 100%;
}
</style>