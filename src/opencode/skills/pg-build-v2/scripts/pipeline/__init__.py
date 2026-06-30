"""Pipeline orchestration package.

各子模块职责：
- state.py: PipelineState frozen dataclass（pipeline 内存状态快照）
- events.py: Event / Record / Action dataclass（事件契约）
- event_log.py: append-only JSONL 写入与读取
- snapshot.py: 最新快照持久化（由 event log 重建）
- reducer.py: 纯函数 reduce_state（无 I/O）
- detect.py: 纯函数 next_pending
- sub_pipeline.py: 递归子 pipeline 容器
- dispatch.py: 构建 action JSON 与 dispatch file
- orchestrator.py: next() / record() / progress() 主循环
"""