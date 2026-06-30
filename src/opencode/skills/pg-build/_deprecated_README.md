# pg-build（旧版，已标记退役）

> **⚠️ 此目录下的代码已标记为 deprecated。新开发请使用 pg-build-v2。**
>
> 此代码仅保留用于回放 archived change 的旧 `.pipeline-state.json`。
> 所有新 session 应使用 `pg-build-v2/`。

## 退役范围

| 文件 | 状态 | 替代 |
|------|------|------|
| `pg-pipeline-runner.py` | ✅ 退役 | `pg-build-v2/scripts/pg-pipeline-runner.py` |
| `pg_runner_v2.py` | ✅ 退役 | 合并入 `pg-build-v2/scripts/pipeline/` |
| `pg_pipeline_state_v2.py` | ✅ 退役 | `pg-build-v2/scripts/pipeline/state.py` |
| `pg_pipeline_common.py` | ✅ 退役 | 拆入 `pg-build-v2/scripts/bootstrap.py` + `pipeline/` |
| `pg_context_chain.py` | ✅ 退役 | `pg-build-v2/scripts/pipeline/context_chain.py` |

## 删除计划

旧代码在 1 个 release cycle（v1.0）后删除。
在此之前，`project.yaml` 的 `state_v2.enabled` 配置项也标记 deprecated（v2 现在是唯一路径）。