## scope creep 检查

**目的**：防止 dev agent 超出 design.md 范围修改无关文件。

### 检查步骤

1. 运行 `git diff feat/pg/<change}` 列出本 track 所有变更文件
2. 对每个变更文件判断：
   - 文件是否在本 track 的 `module_details[].root` 范围内？
   - 文件变更是否与 design.md 的任务描述相关？

### FAIL 判定

- **跨模块修改**：变更文件不在 `module_details[].root` 下（如 backend track 修改了 frontend 代码）
- **无关功能新增**：design.md 未提及的新 API、新 Entity、新表、新字段
- **重构顺手做**：删除/重命名/重构未在 design.md 中列出的代码
- **配置文件越界**：修改了不属于本 track 的环境配置、CI 配置、共享工具类

### 警告（WARN，不阻断）

- 注释、文档、测试 fixture 等小改动
- 自动生成的代码（lombok、protobuf 生成）

### 输出格式

| CV-N | 文件 | 超出范围类型 | 建议 |
|------|------|--------------|------|
| CV-2 | `frontend/src/api/user.ts` | 跨模块修改（backend track 不应改 frontend） | revert |