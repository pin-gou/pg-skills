## Go 模式一致性补充检查

继承自 `default/pattern_consistency.md`，本文件只列**额外**检查项。

### FAIL 判定

- **错误未 wrapping**：`return err` 直接返回，丢失上下文；应 `return fmt.Errorf("xxx: %w", err)`
- **panic 滥用**：业务逻辑用 `panic()` 而非 `return err`
- **未使用 context.Context**：长操作 / I/O 未传 `context.Context`
- **goroutine 无退出语义**：`go func() { ... }()` 没接 ctx.Done() 信号
- **struct tag 缺失**：json/db 序列化字段缺少 tag
- **interface{} 用法**：用了 `interface{}` 而不是 `any`（Go 1.18+）
- **defer 误用**：在循环里 `defer close(ch)` 会延迟到函数结束而非循环结束
- **errcheck 忽略**：未处理 error（`_ = xxx` 而不写明原因）

### 通过条件

- 错误统一用 `fmt.Errorf("...: %w", err)` wrapping
- 业务逻辑不 panic
- 长操作都传 context
- goroutine 监听 ctx.Done()
- struct tag 完整