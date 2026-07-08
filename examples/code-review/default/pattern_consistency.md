## 模式一致性检查

**目的**：验证新增代码与项目现有同类代码在注解/注册/结构上保持一致。

### 检查步骤（语言无关）

1. 对每个**新创建的文件**，找 1-2 个现有同类文件做对照检查
2. 验证：

### FAIL 判定（按语言）

**Java/Spring**：
- 缺少必要注解：`@Service` / `@RestController` / `@Component` / `@Repository`
- Spring DI 未走构造器注入（用 `@Autowired` 字段注入）
- 缺少 `@Slf4j` 等日志注解

**Vue 3**：
- 未使用 `<script setup>` + `defineComponent`
- API 调用未走 `useProTable` 或未抽取为 composable
- 缺少类型声明（`lang="ts"` + interface）

**Go**：
- error 未用 `fmt.Errorf("...%w", err)` wrapping
- goroutine 退出语义不清晰（无 context 取消）
- struct 字段未加 tag（json/db tag 缺失）

### 通过条件

- 注解/装饰器齐全
- 注册/配置（如 Spring `WebSocketConfig`、Vue Router）已更新
- 目录结构与同类模块一致

### 备注

找不到同类文件时（如全新代码类型），至少检查父类/接口/基类的文档要求。