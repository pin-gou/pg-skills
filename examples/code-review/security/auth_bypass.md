## 鉴权绕过检查（Security profile）

### FAIL 判定

- **关键操作前缺少 guard**：
  - `updateXxx` / `deleteXxx` Controller 方法缺少权限检查注解（如 `@PreAuthorize`）
  - gRPC handler / RPC handler 未调 `permission.check()` 就在 db 写入
  - 内部 API 端点被外部访问（缺少 scope 检查）
- **权限校验顺序错误**：先返回数据再 check，应先 check 再返回
- **权限校验可被绕过**：
  - 用户 ID 来自 request body 而非 token（攻击者可改）
  - 资源归属校验用 query string 而非 session
- **未鉴权默认放行**：catch exception 后返回 success（应返回 401/403）

### 通过条件

- 关键操作（写、删除、权限变更）前都有 guard
- guard 顺序正确：先鉴权，再业务逻辑
- 资源归属校验走 session / token，不接受请求参数覆盖