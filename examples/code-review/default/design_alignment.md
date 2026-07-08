## design 对齐检查

**目的**：验证代码实现是否与 `design.md` 中承诺的契约一致。

### 检查步骤

1. 读取 `.pg/changes/<change_name>/design.md`
2. 提取本 track 涉及的承诺：
   - API 端点（HTTP method + path + request/response schema）
   - DTO/Entity 字段（名称、类型、nullable）
   - 数据库 schema 变更（表名、字段名、类型、索引）
   - 消息/事件 payload（topic + schema）
3. 在 `git diff feat/pg/<change>` 的新增/修改文件中验证：

### FAIL 判定（任一即 FAIL）

- **API 缺失**：design.md 列出的端点在代码中找不到对应 Controller / Handler
- **DTO 字段缺失**：design.md 提到的字段在代码 Entity/Model 中不存在
- **DTO 类型不一致**：字段类型与 design.md 不匹配（如 design 用 `Long`，代码用 `Integer`）
- **命名漂移**：design 用 camelCase，代码用 snake_case 等风格不一致
- **必填/可选标记反转**：design 标 nullable，代码用 @NotNull 等

### 输出格式

每条 FAIL 一行：

| CV-N | 设计项 | 代码位置 | 期望 | 实际 |
|------|--------|----------|------|------|
| CV-1 | InstanceVO.id | `InstanceVO.java:12` | Long | Integer |

### 通过条件

所有 design.md 承诺都有对应实现，且关键字段（API 签名、DTO 类型、命名）完全一致。