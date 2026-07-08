## Vue 3 模式一致性补充检查

继承自 `default/pattern_consistency.md`，本文件只列**额外**检查项。

### FAIL 判定

- **未用 Composition API**：仍使用 Options API（`data()` / `methods:` / `computed:`）
- **未用 `<script setup>`**：传统 `<script>` 写法
- **API 调用未抽 composable**：组件内直接 `fetch()` / `axios.get()`，应封装到 `composables/useXxx.ts`
- **类型缺失**：`.vue` 文件无 `<script setup lang="ts">` 或缺 interface 声明
- **列表未用 useProTable**：复杂列表/表格页未用项目统一的 `useProTable`（参见 `webvirt-frontend/src/views/proTable/useProTable/index.vue`）
- **store 直接修改**：Pinia store 未通过 action 修改 state（直接赋值）

### 通过条件

- `<script setup lang="ts">` + `defineComponent`
- API 调用在 composable 中
- 列表/表格用 `useProTable`
- Pinia store 修改走 action