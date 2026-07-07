# 最终结论模板

## 问题修复结论

### 问题
[issue_title]

### 修复状态
[修复成功 / 部分修复 / 修复失败 / 需人工介入]

### 修复强度
verify_level: L1 / L2 / L3

> ⚠️ **L3 时必须标注**：本次修复仅完成编译验证。环境无法启动服务，端到端测试需要人工补跑。

### 问题瀑布
- 初始问题数: N
- 累计暴露问题数: M
- 已修复: K (P-X, P-Y, ...)
- 未修复: L (P-Z, ...)
- 简单达成度: K/M = XX%
- 加权达成度: YY% (按 severity 权重)
- 用户阈值: 80%
- 判定: ...

### 已修复详情

| Problem ID | 描述 | Root Cause | 修复在 Phase | 修复 iteration |
|------------|------|-----------|------------|---------------|
| P-1 | metrics API 1006 | DDL 缺 noise_cpu_percent | 4 | 1 |

### 未修复详情

| Problem ID | 描述 | Severity | 处理方式 |
|------------|------|----------|---------|
| P-2 | vm-disk-stats 401 | major | 子 session: fix-2026-07-08-... |
| P-3 | tab 标题不更新 | minor | KnownIssues |

### 子问题分拆（如有）

- 创建 session: `fix-<date>-<slug>`
- 包含问题: P-2
- 原因: completion_rate < 0.5 AND exposed >= 3

### 根因
[一句话说明根因]

### 修复摘要
[改了什么，列出变更文件]

### 验证结果

#### 成功标准达成情况
| ID | 标准 | 期望值 | 实际值 | 状态 |
|----|------|--------|--------|------|
| SC-FORCE-1 | 运行版本含本次修复符号 | found | found | ✅ |
| SC-1 | metrics API 返回 code=0 | 0 | 0 | ✅ |
| SC-2 | ... | ... | ... | ✅ |
| **达比例** | | | | **3/3 (100%)** |

#### 反例标准触发情况
| ID | 标准 | 触发 | 状态 |
|----|------|------|------|
| FC-1 | 仍返回 1006 | 未触发 | ✅ |
| FC-2 | 日志含 noise_cpu_percent 不存在 | 未触发 | ✅ |

#### Executor 验证摘要（按 verify_level）
- L1: invoke-hook start ✅ / api_call ✅ / log_filter ✅
- L2: mvn compile ✅ / integration test ✅ / DB schema ✅
- L3: mvn compile ✅ / lint ✅ / unit test ✅
- git_diff_check: ✅ (无 DIAG: 残留)

#### Code Review 检查清单
- [✅] 修复只改了目标文件（无连带改动）
- [✅] 遵循项目 API scope 规范
- [✅] 可量化指标已重新测量
- [✅] 静态检查通过
- [✅] executor 验证全部通过
- [✅] 诊断产物已清理
- [✅] **success_criteria 全部通过**
- [✅] **failure_criteria 全部未触发**

### 重试次数
[iteration_count / max_iteration_count] (默认 5)

### 强制终止检测（如有触发）
- [ ] rate_stagnant
- [ ] net_regression
- [ ] problem_explosion
- [ ] same_root_cause

### 备注
[如有必要：Test X 失败与本次修复根因无关，已记入 KnownIssues]

> ⚠️ 如果以上结论与您的实际体验不符，请回复"bug 仍存在"，我将自动回到诊断阶段重新分析。