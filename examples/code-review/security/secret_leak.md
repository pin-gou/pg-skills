## Secret 泄漏检查（Security profile）

### FAIL 判定

- **代码中硬编码 secret**：
  - API key / token / password 出现在源码（任何后缀）
  - 私钥 / 公钥 / 证书（PEM 内容）出现在仓库
  - 数据库连接字符串含密码
  - OAuth client secret、JWT signing key
- **配置文件中明文 secret**：`application.yml` / `application-dev.yml` 等含明文密码
- **日志中打印 secret**：`log.info("token={}", token)` 或 logback pattern 含 `%X{token}`
- **测试 fixture 用了真实 secret**：`test_password = "real_password_xxx"`

### 通过方案

- 所有 secret 走环境变量或 vault
- 配置中 secret 占位：`${DB_PASSWORD}` / `${JWT_SECRET}`
- 日志中 token 用 `***` 脱敏

### 检查命令

```bash
# 扫描硬编码 secret（粗筛）
git diff feat/pg/<change> | grep -E "(password|secret|token|api[_-]?key)\s*[:=]\s*[\"'][^\"']+[\"']"

# 扫描 .env 文件提交
git diff feat/pg/<change> --name-only | grep -E "\.env$|credentials\.json$|\.pem$|\.key$"
```

### 通过条件

- 没有任何硬编码 secret / 密钥 / token
- 所有 secret 通过 env 注入或 vault 获取