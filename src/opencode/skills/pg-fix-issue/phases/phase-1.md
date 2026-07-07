# Phase 1: 调用链路分析

## 必做动作（顺序固定）

- [ ] **S1-1**: 更新 phase-progress.md `current_phase: 1, status: in_progress`
- [ ] **S1-2**: 派遣 `explore` subagent 做全景代码探索
- [ ] **S1-3**: 影响半径扫描（同模式方法/跨模块搜索）
- [ ] **S1-4**: 输出 call_chain_analysis 到 `.pg/fix-issue/<session>/call-chain.md`
- [ ] **S1-5**: 更新 phase-progress.md `phases[1].status: completed`

## S1-2: 派遣 explore

**规则**：所有情况统一派遣 `explore`，不按 track 数分类。

```
Task 工具调用:
  - description: "探索 <具体问题>"
  - prompt: |
      探索目标：[一句话描述你要查的 bug 相关代码]

      关键关注点：
      1. <符号1> 定义在哪、做什么
      2. <符号2> 被谁调用、调用链如何
      3. <关键函数> 的输入输出、边界条件

      输出要求：
      - 文件:行号 + 函数签名
      - 关键代码片段（不超过 20 行/片段）
      - 可能的故障点（基于代码逻辑推断）
  - subagent_type: "explore"
```

explore 返回的摘要**直接**作为 Phase 2 输入。编排器**不重读**已读过的全文。

## S1-3: 影响半径扫描

强制扫描范围（不可跳过）：

| 扫描目录 | 说明 | 检查方法 |
|---------|------|---------|
| 1. 根因函数所在文件 | bug 发生直接位置 | explore 已定位 |
| 2. 同 Controller 同模式方法 | 相同 bug pattern 可能被复制 | grep bug 特征字符串 |
| 3. 同模块/同包的相似逻辑 | 调用模式复制 | grep 关键 token |
| 4. 跨模块的同逻辑 | 其他模块同类工具方法 | 在每个 module 目录 grep |

输出格式：

| 文件 | 为什么受影响 | 是否需要同步修改 |
|------|------------|----------------|
| MetricsSlotRouter.java | createTableWithPartitions DDL 缺列 | ✅ 是 |
| ... | ... | ... |

## S1-4: 输出 call-chain.md

写到 `.pg/fix-issue/<session>/call-chain.md`，格式：

```markdown
## 调用链路分析

### 1. 正向链路
[画出来]

### 2. 反向链路
[画出来]

### 3. 关键代码位置（含受影响 track）
| 链路段 | 文件:行号 | 关键函数 | 候选故障 | 受影响 track |
|--------|----------|---------|---------|------------|

### 4. 候选故障点
| 段 | 候选故障 | 怎么验证 |
|----|---------|---------|

### 5. 影响半径（修复波及的文件）
| 文件 | 为什么受影响 | 是否需要同步修改 |
|------|------------|----------------|
```

## Phase 1 → Phase 2 Gate

进入 Phase 2 前**必须满足**：

- ✅ call-chain.md 已写入
- ✅ affected_files 已识别完毕
- ✅ 影响半径扫描已完成
- ✅ phase-progress.md `phases[1].status = completed`