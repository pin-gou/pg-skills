# Pipeline Progress Monitor

`pg-build` 流水线的只读监控页面。它将终端中的流水线状态、事件和阶段产物整理为可视化界面，帮助开发者快速查看当前执行位置、完成进度、失败原因和疑似停滞情况。

Monitor 读取项目中的 `execution-manifest.yaml`、`pipeline.snapshot.json`、`pipeline.events` 和 `2-build` 产物，不会修改流水线状态。

## 启动

在已经执行 `pg init` 的项目中：

```bash
cd .pg/skills/tools/progress-monitor
pnpm install
pnpm dev
```

从 pg-skills 源码目录监控其他项目时，使用 `--project` 指定项目根目录：

```bash
cd tools/progress-monitor
pnpm install
pnpm dev -- --project D:\path\to\project
```

浏览器打开 <http://127.0.0.1:9323>。

需要 Node.js 22.18 或更高版本。运行中的流水线如果连续 5 分钟没有文件更新，会被标记为“疑似停滞”；可通过参数调整时间：

```bash
pnpm dev -- --project D:\path\to\project --stall-minutes 10
```

## 功能

- **变更列表**：展示 active 和 archived 变更，并显示状态、当前执行位置、完成进度、最后活动时间和疑似停滞标记。
- **Manifest 预览**：以结构化视图或原始 YAML 查看 `execution-manifest.yaml`，了解本次变更启用的 Stage、Track 和 Phase。
- **Progress 追踪**：按 stage → track → phase → fix cycle 展示流水线执行状态并自动刷新。
  - 自动展开当前正在执行的步骤，同时保留用户手动展开的节点。
  - 点击 Phase 或 `final-gate`，可在右侧查看状态、Agent、开始和完成时间、执行摘要与报告。
  - 直接预览 dispatch、report、evidence、result JSON 等阶段产物。
- **Events 日志**：按时间倒序展示 `pipeline.events`，支持分页、全文搜索、失败过滤和原始 JSON 展开。

## 技术栈

- Vue 3 + TypeScript + Vite
- Pinia 状态管理
- Vue Router（hash 模式）
- Vite 开发服务器中间件，只读访问项目的 `.pg/changes/` 数据

## 开发验证

```bash
pnpm test
pnpm build
```

## 端口

默认端口为 `9323`，避免与 project-editor 的 `3008` 端口冲突。
