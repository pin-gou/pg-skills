## Java Spring 模式一致性补充检查

继承自 `default/pattern_consistency.md`，本文件只列**额外**检查项。

### FAIL 判定

- **构造器注入**：Service/Repository 字段用 `@Autowired` 而非构造器注入
  - 正确：`@RequiredArgsConstructor` + `private final FooRepository fooRepository;`
- **日志注解**：Service 缺少 `@Slf4j`，手动 `LoggerFactory.getLogger(...)`
- **事务边界**：写操作 Service 方法缺少 `@Transactional(rollbackFor = Exception.class)`
- **异常处理**：Controller 未用 `@ControllerAdvice` 统一异常处理（应捕获业务异常 → 400/409/500）
- **API 路径**：未使用 `@RequestMapping("/api/...")` 或路径未对齐 K8s scope 风格
- **分页参数**：列表 API 未用 `Page<T>` + `Pageable`，直接 `List<T>` + 手写 limit/offset

### 通过条件

- 注解齐全（`@Service` / `@RestController` / `@RequiredArgsConstructor` / `@Slf4j`）
- 异常走 `@ControllerAdvice` 统一处理
- 写操作有 `@Transactional`
- API 路径对齐 K8s scope 规范