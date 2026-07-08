## 文件位置合规检查

**目的**：防止 dev agent 把代码写到 module 根目录之外。

### 检查步骤

1. 运行 `git diff feat/pg/<change> --name-only` 列出所有变更文件
2. 对比本 track 的 `module_details[].root` 列表
3. 标记所有不在 root 范围内的文件

### FAIL 判定

- 新增/修改的文件路径不在 `module_details[].root` 之下
- 例外允许：
  - `.pg/` 下的变更产物（proposal.md / design.md / tasks.md / 2-build/）
  - 根目录的 README、CI 配置（如 design.md 明确要求）

### 输出格式

| CV-N | 文件 | 期望 root | 实际位置 | 建议 |
|------|------|-----------|----------|------|
| CV-3 | `frontend/src/views/X.vue` | `webvirt-backend/` | `webvirt-frontend/` | 移到 backend 模块或 revert |