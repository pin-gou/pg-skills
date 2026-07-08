## Vue 3 组件 Props 检查

### FAIL 判定

- **类型缺失**：`defineProps<{ ... }>()` 没用 interface / type alias
- **缺 required 标注**：父组件必传的 prop 没有 `required: true`
- **缺默认值**：可选 prop 没有 `default: ...`
- **prop 命名不规范**：用 `camelCase` 而模板中用 `kebab-case` 转换不清晰
- **v-model 滥用**：父子双向绑定 prop 而不是 emit 事件
- **emit 缺类型**：`<script setup>` 中 emit 未定义类型

### 通过条件

- props 有显式 interface/type
- required/default 标注完整
- v-model 仅在受控组件使用
- emit 有类型定义