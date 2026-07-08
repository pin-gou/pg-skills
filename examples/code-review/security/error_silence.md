## 异常吞没检查（Security profile）

### FAIL 判定

- **空 catch 块**：`catch (Exception e) {}` 或 `catch {}`
- **仅 log 不处理**：`catch (e) { log.error(...); }` 后无 `return error` / `throw`
- **catch 后返回默认值**：`catch (e) { return false; }` 让上层以为成功
- **catch Throwable**：吞了 OOM / StackOverflow 等严重错误
- **catch 后未脱敏**：错误信息直接暴露给前端（可能含堆栈、SQL、内部路径）

### 通过方案

- catch 后必须：log + 重新抛出（或返回明确 error）
- 业务异常分类：4xx（用户错误）vs 5xx（系统错误）
- 错误响应脱敏：不暴露 stack trace、SQL、内部路径

### 通过条件

- 所有 catch 都有处理路径（log + 重新抛出 或 返回明确 error）
- 错误响应无敏感信息