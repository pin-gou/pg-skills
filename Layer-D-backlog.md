# Layer D Backlog — pg-skills 扩展点（不在本次 PR 范围）

> 本次 PR（Layer A + Layer C）已完成协议核心 + `actions.health_check`。
> 下列扩展点**不在**本次范围，列为 Layer D backlog，等未来独立 explore。

## D1: `host_actions.<h>.exec` 评估

### 需求来源

Layer C 探索过程中发现：现有 `instance` 级 actions（start/stop/restart/logs/tail/health_check）
覆盖了**服务进程本身**的"生产问题排查"，但**OS 层 / 跨服务 / 主机层**的事实排查
（`ps` / `lsof` / `df` / `journalctl 多 unit` / `virsh list` / `iptables -L`）无法覆盖。

### 候选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. `host_actions.<h>.exec`** + 只读白名单 | 灵活度高；审计可见 | 安全模型复杂；白名单维护成本 |
| **B. `instance.diagnose` action**（多命令组合） | 预定义场景，可控 | 不能 ad-hoc；灵活性低 |
| **C. 让 instance hook 失败时自动 dump 上下文** | 0 新接口；自动 | 不能交互式追问 |
| **D. 现状：让用户自己 SSH** | 0 工作量 | 审计 0 分；agent 无法自动化 |

### 当前判断

- **开发期**（agent 改代码 + 跑测试）：方案 C 足够
- **排查期**（pg-fix-issue 跨 host 联调）：方案 A 是刚需
- **统一标准**：与 Layer A 的"agent 走 pg-invoke-hook.py 唯一入口"一致 → 倾向 A

### 待解决的安全问题（阻碍 Layer D 落地）

1. **只读白名单怎么维护**：role 不同白名单不同（backend / frontend / agent）
2. **跨 host 怎么执行**：localhost vs box-1 vs box-2 的 SSH 注入
3. **审计粒度**：每条 exec 命令独立 result.json？还是合并？
4. **destructive 命令防护**：`rm /` / `mkfs` / `shutdown` 怎么拦？靠白名单穷举？还是 AST 分析？

### 相关探索记录

- v7 决策：`pg-agent` caller 已就绪，可承载 `host_actions.exec` 的 caller 校验
- 已讨论过 `restrict_to: pg-agent` schema 字段强制
- 已讨论过 `pg_log_dir_for_skill` 路由表同步

## D2: 跨服务日志聚合（场景 ⑥）

### 需求

agent 排查一个请求失败涉及 backend + agent + libvirt 多个服务时，需要聚合多个 unit 的 journalctl 输出。

### 候选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. agent 上报聚合日志** | protocol 层级清晰 | 需要改 agent proto；agent 端开发工作 |
| **B. `host_actions.exec` 跑 `journalctl -u a -u b --since`** | 复用 D1 的 hook | 依赖 D1 |
| **C. backend 把每个 request 的 X-Request-Id 关联到所有调用方** | 后端可控 | 需要 backend 改造 |

### 当前判断

倾向 **B**（复用 D1），**A** 是协议层最优解但工作量大。

## D3: `instance.diagnose` action

### 需求

预定义诊断 bundle（如"backend 启动失败" 自动 dump ps/lsof/df/jctl 末尾）

### 当前判断

与 D1 互补——D1 是 ad-hoc，D3 是预定义。两类都做的话 schema 复杂。
**先做 D1**，看真实使用模式再决定 D3 是否必要。

## D4: iptables / firewall 类诊断

### 需求

排查网络问题（iptables / firewall 阻断）

### 当前判断

如果 D1 落地，iptables 走白名单（`iptables -L` 是只读）即可。
**不独立做**，等 D1 落地后再补白名单。

## D5: SKILL.md 自身缺 SSOT 发现机制引导

### 需求

5 个 SKILL 入口（`pg-build` / `pg-fix-issue` / `pg-propose` / `pg-verify-and-merge` / `pg-quick-build`）
的 SKILL.md 里**没有一个**明确告诉 LLM "你应该调 `pg-parse-config.py pg-agent` 而不是 `pg-fix-issue`"。

### 候选方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. 每个 SKILL.md 加一节 "agent 入口"** | 全面 | 工作量大；5 个文件都要改 |
| **B. opencode prompt 注入（pg-skills 自动注入）** | 一次搞定 | 改 opencode 配置 |
| **C. 写 .pg/context/agent-protocol.md 已被强制引用** | 已落地 | 只对用户项目生效，SKILL 自身不知 |

### 当前判断

**A 是 Layer A 的延伸**，但**本次未做**——Layer A 改的是 L1 协议层，SKILL.md 改的是 L2 skill 层，scope 不同。

## D6: `modules.<m>.test.{unit,integration}` 引用 `.pg/hooks/<x>-test.sh` 灰色地带

### 问题

当前 oc1-web-virt 项目 `modules.backend.test.integration = ".pg/hooks/backend-test.sh"`,
`modules.agent.test.unit = ".pg/hooks/agent-test.sh"` 等——这些是 inline `executable_command`
还是走 hook 协议？

### 当前判断

**灰色地带**：形式上是 inline `executable_command`（runner 渲染为 `bash -c '<cmd>'`），但 `<cmd>` 内部
又调用 hook 脚本——形成"双重封装 + 双重 timeout"反模式。

**真正解决**：让 `modules.<m>.test.<key>` 不再引用 `.pg/hooks/<m>-test.sh`（直接 inline 写测试命令）。
但这是项目侧决策，pg-skills 只能"建议改"，不能强改。

### Layer D 之外的原因

属于历史包袱清理，不是协议扩展。

## D7: orchestrate / migrate / metrics 扩展点

| 扩展点 | 状态 |
|--------|------|
| `actions.orchestrate`（跨 role 编排） | 复杂度高，未有用户需求 |
| `actions.migrate`（flyway） | maven plugin 已覆盖 |
| `actions.metrics`（CPU/内存指标） | 用户需求未明确 |
| `actions.dump`（日志归档到 artifacts） | 可用现有 logs + cp 替代 |

### 当前判断

**全部延后**，等真实需求出现再独立 explore。

## 总结

| ID | 主题 | 优先级 | 阻塞原因 |
|----|------|--------|----------|
| D1 | `host_actions.exec` | high | 安全模型待定（D1 内部 4 子问题） |
| D2 | 跨服务日志聚合 | medium | 依赖 D1 |
| D3 | `instance.diagnose` | low | 依赖 D1 后的真实使用 |
| D4 | iptables / firewall 诊断 | low | 依赖 D1 白名单 |
| D5 | SKILL.md 引导机制 | medium | scope 在 L2，不在 L1 |
| D6 | modules.test 灰色地带 | low | 项目侧决策 |
| D7 | orchestrate/migrate/metrics | low | 用户需求未明 |

**下一步**：D1 优先；其它等 D1 落地或新需求出现再启动。