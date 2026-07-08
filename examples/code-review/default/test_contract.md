## 测试契约一致性检查

**目的**：验证 test agent 写的测试是否真的覆盖 design.md 中描述的契约。

### 检查步骤

1. 读取 design.md 中的契约声明（"应该返回 X"、"应该抛出 Y"、"应该调用 Z 次数"）
2. 在 `git diff feat/pg/<change>` 中找新增/修改的测试文件
3. 验证：

### FAIL 判定

- **断言不严格**：使用 `toBeDefined()` / `toBeTruthy()` 等弱断言代替 `toBe(expectedValue)`
- **契约未覆盖**：design.md 提到的关键行为没有对应测试
- **场景遗漏**：只有 happy path，没有 error path / edge case
- **命名不反映场景**：`it('returns false')` 而不是 `it('rejects handshake when version mismatch')`

### 通过条件

- 关键契约每条都有强断言测试
- 命名反映业务场景
- happy path + 至少一个 error path

### 输出格式

| CV-N | 设计契约 | 测试文件 | 断言强度 | 建议 |
|------|----------|----------|----------|------|
| CV-4 | "重复 name 应抛 409" | `InstanceServiceTest.java:45` | `toBeTruthy()` | 改为 `assertThrows(ConflictException.class, ...)` |