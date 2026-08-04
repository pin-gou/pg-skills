# Pipeline Progress Monitor

pg-build 流水线执行进度可视化工具。只读 UI，实时展示变更的执行进度。

## 启动

```bash
cd .pg/skills/tools/progress-monitor
pnpm install
pnpm dev
```

浏览器打开 http://localhost:9323

## 功能

- **变更列表**：展示所有 active 和 archived 变更，带状态徽标
- **Manifest 预览**：查看 `execution-manifest.yaml` 内容（只读 YAML 展示）
- **Progress 追踪**：树形结构展示 stage → track → phase → fix cycle 层级，实时轮询刷新
  - 自动展开到当前 `in_progress` 节点
  - 右侧预览面板显示阶段摘要和关联产物（dispatch / report / result.json / evidence）
  - 产物直接内联预览，无需弹窗
- **事件日志**：`pipeline.events` 翻页查看

## 技术栈

- Vue 3 + TypeScript + Vite
- Pinia 状态管理
- Vue Router (hash 模式)
- Vite 内嵌 middleware 读取 `.pg/changes/` 文件系统

## 端口

9323（与 project-editor 的 3008 错开）