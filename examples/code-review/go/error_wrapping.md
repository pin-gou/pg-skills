## Go 错误处理检查

### FAIL 判定

- **错误信息缺上下文**：`return errors.New("failed")` 不带变量值/位置
  - 正确：`return fmt.Errorf("read config %s: %w", path, err)`
- **errors.Is/As 缺失**：需要判别特定错误时用 `==` 比较或字符串匹配
  - 正确：`if errors.Is(err, sql.ErrNoRows) { ... }`
- **错误吞掉**：catch 后只 log 不返回上层
  - 正确：log 后 `return fmt.Errorf("xxx: %w", err)`
- **sentinel error 散落**：业务错误定义在多处，应集中在 `errors.go`

### 通过条件

- 错误信息含上下文（变量值、操作名、关键参数）
- 使用 `errors.Is` / `errors.As` 而非字符串比较
- 错误逐层 wrapping 而非吞掉
- 业务 sentinel error 集中定义