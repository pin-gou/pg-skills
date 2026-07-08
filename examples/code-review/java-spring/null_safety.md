## Java null 安全检查

### FAIL 判定

- **返回 null**：Service 方法返回 `null` 而非 `Optional<T>`
- **Optional 滥用**：用 `Optional` 作为字段类型或方法参数（应仅作为返回值）
- **链式调用**：`.get()` / `.getXxx()` 未做 null 检查就直接调用（如 `user.getAddress().getCity()`）
- **@NotNull vs @Nullable**：缺省标注导致调用方无法判断
- **空集合返回 null**：应返回空 `List`/`Map` 而非 null
- **三元表达式嵌套 null**：`x != null ? x.y : null` 难以阅读，应早返回

### 通过条件

- Service 返回类型为 `Optional<T>` 或明确非 null
- 字段访问有 null check
- 集合返回空集合而非 null

### 备注

对 `@NonNull` / `@Nullable` 注解（lombok / JSR-305）的支持是**加分项**。