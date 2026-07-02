"""Orchestrator — pipeline 编排循环。

提供 next() / record() / progress() 方法给 CLI 入口。"""

from __future__ import annotations

import json
import os
from typing import Any

from pipeline.event_log import EventLog
from pipeline.snapshot import load_snapshot, save_snapshot
from pipeline.state import PipelineState, TrackState, PhaseState
from pipeline.events import (
    FINAL_GATE_TRACK,
    PipelineRecord,
    PipelineAction,
    EVT_PIPELINE_STARTED,
    EVT_DISPATCH_STARTED,
    EVT_RECORD_RECEIVED,
    EVT_TRACK_COMPLETED,
    EVT_PIPELINE_COMPLETED,
    EVT_WORKFLOW_FAILED,
    EVT_FIX_CYCLE_STARTED,
    EVT_GIT_COMMIT,
    EVT_PREPARE_ENV_COMPLETED,
    EVT_CLEAN_ENV_COMPLETED,
)
from pipeline.reducer import reduce_state
from pipeline.detect import next_pending
from pipeline.sub_pipeline import FIX_CYCLE, GATE_FIX_CYCLE
from pipeline.dispatch import build_action, build_final_gate_action
from pipeline.config import (
    load_project_config,
    resolve_module_details,
    resolve_module_roots,
    resolve_test_commands,
    resolve_env_instances,
    resolve_hooks,
)
from pipeline.tasks_md import extract_section_content
from pipeline.sub_agent_contract import validate_record_args
from pipeline.replay import replay_state
import bootstrap
import subprocess


CHANGES_DIR = os.path.join(bootstrap.PROJECT_ROOT, ".pg", "changes")


def _change_root(change: str) -> str:
    return os.path.join(CHANGES_DIR, change)


class Orchestrator:
    """Pipeline 编排器。

    状态管理：
      - state = load_snapshot(change_root)  # cold start
      - event_log = EventLog(change_root)
      - 记录 dispatch 和 record 事件到 event_log
      - reduce_state(state, record) 产出 (new_state, action)
      - save_snapshot(change_root, new_state)

    v2.1 checkpoint / resume：
      - 默认从 snapshot.json 加载（快速）
      - snapshot 损坏时自动 fallback 到 pipeline.events replay
      - 显式 use_replay=True 强制走 events 重建（用于 debug / 时间旅行）

    Args:
        change: change 名称或相对路径（支持 'archive/2026-07-02-grpc-query-correlation'
                这种带子目录的形式）
        use_replay: 强制从 events 重建 state
    """

    def __init__(self, change: str, use_replay: bool = False):
        self.change = os.path.basename(change.rstrip("/")) if "/" in change else change
        # v2.1: 兼容 archive/<date>-<name> 路径
        if "/" in change:
            self.change_root = os.path.join(CHANGES_DIR, change)
        else:
            self.change_root = _change_root(change)
        self.event_log = EventLog(change_root=self.change_root)

        if use_replay:
            # v2.1: 强制从 events 重建 state
            self.state = replay_state(self.change_root)
            self._loaded_via = "replay"
        else:
            self.state = load_snapshot(self.change_root) or PipelineState(change=self.change)
            self._loaded_via = "snapshot"

    def next(self) -> dict[str, Any]:
        """返回下一个 action JSON。

        首次调用时执行 bootstrap（如果未初始化）。
        """
        # bootstrap 状态检查
        if not self.state.pipeline_order:
            # 首次调用：执行 bootstrap + 读取 pipeline_order
            return self._first_next()

        # 检查是否已 terminal
        if self.state.status == "completed":
            return {"action": "done", "status": "completed"}
        if self.state.status == "failed":
            return {
                "action": "workflow_failed", "fatal": True,
                "reason": self.state.failed_reason or "unknown",
            }

        # 下一步 dispatch
        action = next_pending(self.state)
        return self._action_to_dict(action)

    def _first_next(self) -> dict[str, Any]:
        """首次 next：执行 bootstrap 并设置 pipeline_order。"""
        # 执行 bootstrap
        try:
            boot_result = bootstrap.run_bootstrap(
                self.change,
                init_committed=self.state.init_committed,
                event_log=self.event_log,
            )
        except bootstrap.EnvHookError as e:
            return {
                "action": "env_hook_failed",
                "phase": e.phase_name,
                "log_path": e.log_path,
                "exit_code": e.exit_code,
                "fatal": True,
                "reason": f"env-hook {e.phase_name} failed (exit_code={e.exit_code})",
                "error_category": e.error_category,
                "error_message": e.error_message,
                "error_hint": e.error_hint,
            }

        # 标记 init_committed
        if boot_result.get("init_commit") and boot_result["init_commit"].get("committed"):
            self.state = self.state.replace(init_committed=True)

        # 找 pipeline_order + track 配置 + stage 配置
        order, track_configs = self._detect_pipeline_config()
        stage_order, stage_env_map = self._detect_stage_config()

        # 读取 project.yaml 富化上下文
        project_config = load_project_config(bootstrap.PROJECT_ROOT)
        SUB_PHASE_NAMES = ("test", "dev", "verify", "gate")

        tracks: dict[str, TrackState] = {}
        track_types: dict[str, str] = {}
        for tid in order:
            if tid == FINAL_GATE_TRACK:
                continue
            cfg = track_configs.get(tid, {})
            bare = tid.rsplit(".", 1)[-1]
            module_names = cfg.get("modules", [])
            stage_name = PipelineState.extract_stage(tid)
            env_name = stage_env_map.get(stage_name, "dev-local")

            # 解析 tasks.md 各 phase 内容
            tasks_by_phase: dict[str, str] = {}
            for pname in SUB_PHASE_NAMES:
                content = extract_section_content(self.change_root, tid, pname)
                if content:
                    tasks_by_phase[pname] = content

            tracks[tid] = TrackState.create(
                tid,
                modules=tuple(module_names),
                max_fail_retries=cfg.get("max_fail_retries", 3),
                max_fix_retries=cfg.get("max_fix_retries", 5),
                max_gate_fix_retries=cfg.get("max_gate_fix_retries", 2),
                module_roots=resolve_module_roots(project_config, module_names),
                module_details=resolve_module_details(project_config, module_names),
                test_commands=resolve_test_commands(project_config, module_names),
                review_level=cfg.get("review_level", ""),
                env_name=env_name,
                env_instances_yaml=resolve_env_instances(project_config, env_name),
                hooks_yaml=resolve_hooks(project_config, env_name),
                prepare_status="ok",
                label=cfg.get("description", bare),
                tasks_by_phase=tasks_by_phase,
                commands=tuple(cfg.get("commands", [])),
            )
            if cfg.get("type") == "simple":
                track_types[tid] = "simple"

        # 确定当前 stage
        first_stage = PipelineState.extract_stage(order[0]) if order else ""

        self.state = self.state.replace(
            pipeline_order=tuple(order),
            tracks=tracks,
            track_types=track_types,
            stage_order=tuple(stage_order),
            stage_env_map=stage_env_map,
            current_stage=first_stage,
            status="running",
        )

        # 标记第一个 stage 为已 prepared（由 bootstrap 完成）
        if first_stage:
            self.state = self.state.replace(
                stage_prepared={first_stage},
            )

        save_snapshot(self.change_root, self.state)
        self.event_log.append(EVT_PIPELINE_STARTED, {
            "change": self.change,
            "pipeline_order": list(self.state.pipeline_order),
        })

        # 返回第一个 dispatch
        action = next_pending(self.state)
        return self._action_to_dict(action)

    def _detect_pipeline_config(self) -> tuple[list[str], dict[str, dict]]:
        """读取 execution-manifest.yaml + project.yaml 获取 pipeline order 和 track 配置。

        Returns:
            (order, track_configs):
                order: 有序的 track id 列表
                track_configs: {track_id: {modules, max_fail_retries, max_fix_retries, max_gate_fix_retries, type}}
        """
        order: list[str] = []
        manifests_used = False

        # 先从 manifest 读 order
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                import yaml as _yaml
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml.safe_load(f) or {}
                for stage in manifest.get("stages", []):
                    stage_name = stage.get("name", "")
                    for track in stage.get("tracks", []):
                        tid = track["id"] if isinstance(track, dict) else track
                        qualified = f"{stage_name}.{tid}" if stage_name else tid
                        order.append(qualified)
                if "final_gate" in manifest:
                    order.append(FINAL_GATE_TRACK)
                if order:
                    manifests_used = True
            except Exception:
                pass

        # fallback: project.yaml
        if not order:
            config_path = os.path.join(bootstrap.PROJECT_ROOT, ".pg", "project.yaml")
            if os.path.isfile(config_path):
                try:
                    import yaml as _yaml
                    with open(config_path, encoding="utf-8") as f:
                        config = _yaml.safe_load(f) or {}
                    for stage in config.get("stages", []):
                        stage_name = stage.get("name", "")
                        for t in stage.get("tracks", []):
                            qualified = f"{stage_name}.{t}" if stage_name else t
                            order.append(qualified)
                    if order:
                        manifests_used = True
                except Exception:
                    pass

        track_configs: dict[str, dict] = {}

        # 从 manifest 读 track 级配置（含 simple track commands）
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                import yaml as _yaml
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml.safe_load(f) or {}
                for stage in manifest.get("stages", []):
                    stage_name = stage.get("name", "")
                    for track in stage.get("tracks", []):
                        tid = track["id"] if isinstance(track, dict) else track
                        qualified = f"{stage_name}.{tid}" if stage_name else tid
                        if qualified not in track_configs:
                            track_configs[qualified] = {}
                        if isinstance(track, dict):
                            cmds = track.get("commands", [])
                            if cmds:
                                track_configs[qualified]["commands"] = cmds
            except Exception:
                pass

        # 从 project.yaml 读 track 级配置
        config_path = os.path.join(bootstrap.PROJECT_ROOT, ".pg", "project.yaml")
        if os.path.isfile(config_path):
            try:
                import yaml as _yaml
                with open(config_path, encoding="utf-8") as f:
                    config = _yaml.safe_load(f) or {}
                tracks_cfg = config.get("tracks", {})
                for tid in order:
                    if tid == FINAL_GATE_TRACK:
                        continue
                    bare = tid.rsplit(".", 1)[-1]
                    cfg = tracks_cfg.get(bare, {})
                    track_configs[tid] = {
                        "modules": cfg.get("modules", []),
                        "max_fail_retries": cfg.get("max_fail_retries", 3),
                        "max_fix_retries": cfg.get("max_fix_retries", 5),
                        "max_gate_fix_retries": cfg.get("max_gate_fix_retries", 2),
                        "type": cfg.get("type", "standard"),
                        "review_level": cfg.get("review_level", ""),
                        "description": cfg.get("description", ""),
                        "fix_routing": cfg.get("fix_routing", "source"),
                    }
            except Exception:
                pass

        return order, track_configs

    def _detect_stage_config(self) -> tuple[list[str], dict[str, str]]:
        """从 execution-manifest.yaml 读取 stage 顺序和环境映射。

        Returns:
            (stage_order, stage_env_map):
                stage_order: 有序的 stage 名称列表
                stage_env_map: {stage_name: env_name}
        """
        stage_order: list[str] = []
        stage_env_map: dict[str, str] = {}

        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                import yaml as _yaml
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml.safe_load(f) or {}
                for stage in manifest.get("stages", []):
                    name = stage.get("name", "")
                    if not name:
                        continue
                    stage_order.append(name)
                    env = stage.get("environment", "")
                    if isinstance(env, str):
                        stage_env_map[name] = env
                    elif isinstance(env, dict):
                        stage_env_map[name] = env.get("name", "dev-local")
                    else:
                        stage_env_map[name] = "dev-local"
            except Exception:
                pass

        if not stage_order:
            stage_order.append("dev")

        return stage_order, stage_env_map

    def _detect_pipeline_order(self) -> tuple[str, ...]:
        """从 execution-manifest.yaml 读取 pipeline order。

        fallback 到 project.yaml stages。
        """
        # 尝试 manifest
        manifest_path = os.path.join(self.change_root, "execution-manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                import yaml as _yaml
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = _yaml.safe_load(f) or {}
                order = []
                for stage in manifest.get("stages", []):
                    stage_name = stage.get("name", "")
                    for track in stage.get("tracks", []):
                        tid = track["id"] if isinstance(track, dict) else track
                        qualified = f"{stage_name}.{tid}" if stage_name else tid
                        order.append(qualified)
                if "final_gate" in manifest:
                    order.append(FINAL_GATE_TRACK)
                if order:
                    return tuple(order)
            except Exception:
                pass

        # fallback: project.yaml
        config_path = os.path.join(bootstrap.PROJECT_ROOT, ".pg", "project.yaml")
        if os.path.isfile(config_path):
            try:
                import yaml as _yaml
                with open(config_path, encoding="utf-8") as f:
                    config = _yaml.safe_load(f) or {}
                order = []
                for stage in config.get("stages", []):
                    stage_name = stage.get("name", "")
                    for t in stage.get("tracks", []):
                        qualified = f"{stage_name}.{t}" if stage_name else t
                        order.append(qualified)
                return tuple(order)
            except Exception:
                pass

        return ()

    def record(
        self,
        status: str,
        report_path: str = "",
        summary: str = "",
        outputs: str = "",
        issues: str = "",
    ) -> dict[str, Any]:
        """记录一次 sub-agent 完成事件。

        status: completed | failed | escalate | pass | fail
        """
        # 从当前 state 推断 track/phase
        track = self.state.current_track
        phase = self.state.current_phase

        if not track or not phase:
            return {"action": "error", "fatal": False,
                    "reason": "No active item to record"}

        # ── Sub-agent 返回契约校验（v2.1）──
        # 失败时不推进 state、不写 event log，直接返回 error action
        ok, reason = validate_record_args(
            phase=phase,
            track=track,
            status=status,
            summary=summary,
            report_path=report_path,
            outputs=outputs,
            issues=issues,
        )
        if not ok:
            return {
                "action": "error",
                "fatal": True,
                "reason": reason,
                "phase": phase,
                "track": track,
                "hint": (
                    "重新派遣 sub-agent 并显式要求其按 Sub-agent 返回契约返回 JSON。"
                    "Prompt 模板已在 v2.1 增加 SUB_AGENT_RETURN_CONTRACT 段。"
                ),
            }

        # 构建 record
        record = PipelineRecord(
            track=track,
            phase=phase,
            status=status,
            summary=summary,
            report_path=report_path or None,
            issues=issues,
        )

        # reducer
        new_state, action = reduce_state(self.state, record)

        # 写 event log
        event_data = {
            "track": track,
            "phase": phase,
            "status": status,
            "summary": summary,
            "report_path": report_path or None,
            "issues": issues,
        }
        self.event_log.append(EVT_RECORD_RECEIVED, event_data, snapshot_after=new_state.to_dict())

        # 记录辅助事件（track_completed / fix_cycle_started 等）
        if action.kind == "advance" and track:
            if new_state.is_track_completed(track):
                self.event_log.append(EVT_TRACK_COMPLETED, {"track": track})

        if status == "escalate" and phase == "verify":
            verify = new_state.tracks.get(track, TrackState.create(track)).phases.get("verify", PhaseState())
            cycle = len(verify.fix_cycles)
            self.event_log.append(EVT_FIX_CYCLE_STARTED, {"track": track, "cycle": cycle, "source_report": report_path})

        # 更新 state
        self.state = new_state
        save_snapshot(self.change_root, new_state)

        # 同步 tasks.md checkbox（Item 2）
        if status in ("completed", "pass"):
            try:
                from pipeline.tasks_md import mark_phase_completed
                mark_phase_completed(self.change_root, track, phase)
            except Exception:
                pass

        # 自动 git commit（Item 3）
        auto_commit = self._auto_commit(status, track, phase)

        # 构建返回
        result = self._action_to_dict(action)

        if auto_commit:
            result["commit"] = auto_commit

        return result

    def progress(self) -> dict[str, Any]:
        """返回当前 pipeline 进度。"""
        tracks = {}
        for tid, t in self.state.tracks.items():
            phases = {}
            for pname, ph in t.phases.items():
                phases[pname] = ph.status
            tracks[tid] = {
                "status": t.status,
                "phases": phases,
            }

        return {
            "change": self.change,
            "status": self.state.status,
            "current_track": self.state.current_track,
            "current_phase": self.state.current_phase,
            "tracks": tracks,
            "event_count": self.event_log.count(),
            "has_sub_pipeline": self.state.current_sub_pipeline is not None,
        }

    def _action_to_dict(self, action: PipelineAction) -> dict[str, Any]:
        """把 PipelineAction 转为标准 action JSON。

        dispatch 路径通过 dispatch.build_action() 写入 dispatch_file，
        final-gate 通过 dispatch.build_final_gate_action() 写入 dispatch_file。

        v2.1 新增：final-gate 派遣前做 gate-assessment.md 存在性预检，
        任何一个 track 的 gate assessment 缺失则阻断 final-gate 派遣，
        返回 workflow_failed（fatal=True），避免 final-gate 在残缺数据上 pass。
        """
        if action.kind == "dispatch":
            is_final = action.track == FINAL_GATE_TRACK
            if is_final:
                # ── v2.1: final-gate 前置门控 ──
                # 检查所有非 simple track 是否有有效的 gate-assessment 报告
                missing = self._collect_missing_gate_assessments()
                if missing:
                    # 阻断 final-gate：返回 workflow_failed
                    reason = (
                        f"final-gate 派遣前门控失败：以下 {len(missing)} 个 track "
                        f"缺少 gate assessment 报告: {', '.join(missing)}。"
                        f"编排器应回到缺失 track 重新跑 gate。"
                    )
                    self.event_log.append(EVT_WORKFLOW_FAILED, {"reason": reason})
                    self.state = self.state.replace(
                        status="failed", failed_reason=reason,
                    )
                    save_snapshot(self.change_root, self.state)
                    return {
                        "action": "workflow_failed",
                        "fatal": True,
                        "reason": reason,
                        "missing_gate_assessments": missing,
                    }

                result = build_final_gate_action(self.state, self.change_root)
            else:
                result = build_action(self.state, action, self.change_root)

            # 写 dispatch_started event
            self.event_log.append(EVT_DISPATCH_STARTED, {
                "track": action.track,
                "phase": action.phase,
                "cycle": action.cycle,
                "agent": result.get("agent", ""),
                "dispatch_file": result.get("dispatch_file", ""),
            })
            # 更新 state 的 current_track/current_phase
            self.state = self.state.replace(
                current_track=action.track,
                current_phase=action.phase,
            )
            save_snapshot(self.change_root, self.state)
            return result

        if action.kind == "advance":
            return self.next()

        if action.kind == "done":
            self.event_log.append(EVT_PIPELINE_COMPLETED, {"final_status": "completed"})
            self.state = self.state.replace(status="completed")
            save_snapshot(self.change_root, self.state)
            auto_commit = self._auto_commit("completed", "final-gate", "completed")

            archive_result = self._auto_archive()

            affected = [
                t.bare
                for tid in self.state.pipeline_order
                if tid != FINAL_GATE_TRACK
                for t in [self.state.tracks.get(tid)]
                if t and t.status == "completed"
            ]

            result = {
                "action": "done",
                "status": "completed",
                "next_action": "verify_and_merge",
                "affected_tracks": affected,
                "archive": archive_result,
            }
            if auto_commit:
                result["commit"] = auto_commit
            return result

        if action.kind == "workflow_failed":
            reason = action.detail.get("reason", "unknown")
            self.event_log.append(EVT_WORKFLOW_FAILED, {"reason": reason})
            self.state = self.state.replace(status="failed", failed_reason=reason)
            save_snapshot(self.change_root, self.state)
            return {"action": "workflow_failed", "fatal": True, "reason": reason}

        if action.kind == "bootstrap":
            # bootstrap 已在 _first_next 处理，不应到这里
            return self.next()

        if action.kind == "error":
            return {
                "action": "error",
                "fatal": False,
                "reason": action.detail.get("reason", "unknown"),
            }

        if action.kind == "noop":
            return self.next()

        if action.kind == "env_switch":
            return self._handle_env_switch(action)

        return {"action": action.kind, **action.detail}

    def _handle_env_switch(self, action: PipelineAction) -> dict[str, Any]:
        """处理 env_switch action：执行 prepare_env 或 clean_env 脚本。"""
        phase = action.phase
        env_name = action.detail.get("env_name", "")
        stage = action.detail.get("stage", "")

        event_type = EVT_PREPARE_ENV_COMPLETED if phase == "prepare_env" else EVT_CLEAN_ENV_COMPLETED

        # 执行 env hook
        try:
            env_result = bootstrap.execute_env_hook_inline(self.change, phase)
        except Exception as e:
            env_result = {"success": False, "error": str(e)}

        self.event_log.append(event_type, {
            "success": env_result.get("success", False),
            "skipped": env_result.get("skipped", False),
            "exit_code": env_result.get("exit_code"),
            "log_path": env_result.get("log_path"),
            "env_name": env_name,
            "stage": stage,
        })

        if not env_result.get("success"):
            if not env_result.get("skipped"):
                return {
                    "action": "env_hook_failed",
                    "phase": phase,
                    "log_path": env_result.get("log_path", ""),
                    "exit_code": env_result.get("exit_code", -1),
                    "fatal": True,
                    "reason": f"env-hook {phase} failed (exit_code={env_result.get('exit_code', -1)}, env={env_name})",
                    "error_category": env_result.get("error_category", "unknown"),
                    "error_message": env_result.get("error_message", ""),
                    "error_hint": env_result.get("error_hint", ""),
                }

        # 更新 stage_prepared 状态
        new_prepared = set(self.state.stage_prepared)
        if phase == "prepare_env":
            new_prepared.add(stage)
            self.state = self.state.replace(
                current_stage=stage,
                stage_prepared=new_prepared,
            )
        elif phase == "clean_env":
            new_prepared.discard(stage)
            self.state = self.state.replace(stage_prepared=new_prepared)

        save_snapshot(self.change_root, self.state)

        # 继续推进 pipeline（返回下一个 action）
        return self.next()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """执行 git 命令（在项目根目录）。"""
        return subprocess.run(
            ["git", *args],
            capture_output=True, text=True,
            cwd=bootstrap.PROJECT_ROOT,
        )

    def _auto_commit(self, status: str, track: str, phase: str) -> dict[str, Any]:
        """record 后自动 git commit（仅在 pipeline 运行时执行）。

        每次 record 都创建 chore(<change>): auto-record ... 提交。
        **这是设计意图——编排期间的原子化审计痕迹**，让每一步修改都可追溯。
        pg-verify-and-merge Phase 1 会 squash 所有编排期 commit 为单条 feat(…): … 进入 master。
        master 历史不产生任何噪音。

        Args:
            status: 当前 record 的 status
            track: 当前 track id
            phase: 当前 phase

        Returns:
            dict:
                - attempted=True, committed=True → 实际创建了 git commit
                - attempted=True, committed=False → 工作区干净或 commit 失败
        """
        porcelain = self._git("status", "--porcelain").stdout.strip()
        if not porcelain:
            return {
                "attempted": True, "committed": False,
                "reason": "工作区干净，无可提交内容",
            }

        self._git("add", "-A")
        msg = f"chore({self.change}): auto-record {track}:{phase} {status}"
        r = self._git("commit", "-m", msg)
        if r.returncode == 0:
            sha = self._git("rev-parse", "HEAD").stdout.strip()
            self.event_log.append(EVT_GIT_COMMIT, {
                "sha": sha, "message": msg, "branch": f"feat/pg/{self.change}",
            })
            return {
                "attempted": True, "committed": True, "sha": sha,
                "message": msg, "reason": None,
            }
        return {
            "attempted": True, "committed": False, "sha": None,
            "message": msg, "reason": r.stderr.strip() or "commit failed",
        }

    def _collect_missing_gate_assessments(self) -> list[str]:
        """收集所有缺少 gate assessment 报告的 track id。

        v2.1: 用于 final-gate 派遣前门控。

        规则：
          - 只检查 status == "completed" 的 track（避免查未完成的）
          - simple track 跳过（无 gate-assessment）
          - gate assessment 路径约定: 2-build/{seq}-{track}-gate-report.md
            或 *.gate.md / *.gate-assessment.md

        Returns:
            缺少 gate assessment 的 track id 列表
        """
        build_dir = os.path.join(self.change_root, "2-build")
        if not os.path.isdir(build_dir):
            return [t for t in self.state.pipeline_order
                    if t != FINAL_GATE_TRACK
                    and not self._is_simple_track(t)]

        existing = set(os.listdir(build_dir))
        missing: list[str] = []

        for tid in self.state.pipeline_order:
            if tid == FINAL_GATE_TRACK:
                continue
            if self._is_simple_track(tid):
                continue
            t = self.state.tracks.get(tid)
            if not t or t.status != "completed":
                continue

            # 检查是否存有该 track 的 gate assessment
            # 文件命名约定（dispatch.py 实际产出）:
            #   {seq}-{track}-gate.md            ← 实际命名（如 006-dev.backend-gate.md）
            #   {seq}-{track}-gate-assessment.md ← SKILL 文档约定
            #   {seq}-{track}-gate-report.md     ← 旧命名
            track_bare = tid.rsplit(".", 1)[-1]
            candidates = [
                f for f in existing
                if f.endswith(".md")
                and (
                    # 实际命名：必须包含完整 track id + "-gate" 后缀
                    f.endswith(f"-{track_bare}-gate.md")
                    or f.endswith(f"-{track_bare}-gate-assessment.md")
                    or f.endswith(f"-{track_bare}-gate-report.md")
                    # 也兼容：完整 track id 带 -gate 后缀
                    or f.endswith(f"-{tid}-gate.md")
                    or f.endswith(f"-{tid}-gate-assessment.md")
                    or f.endswith(f"-{tid}-gate-report.md")
                )
            ]
            if not candidates:
                missing.append(tid)

        return missing

    def _is_simple_track(self, track_id: str) -> bool:
        """判断 track 是否为 simple track（无需 gate-assessment）。"""
        track_types = getattr(self.state, "track_types", {}) or {}
        return track_types.get(track_id) == "simple"

    def _auto_archive(self) -> dict[str, Any]:
        """调用 pg-archive.py 归档 change 目录 + git commit 归档。"""
        script = os.path.join(
            bootstrap.PROJECT_ROOT, ".opencode", "skills", "pg-archive", "scripts", "pg-archive.py"
        )
        r = subprocess.run(
            ["python3", script, "move", self.change, "--project-root", bootstrap.PROJECT_ROOT],
            capture_output=True, text=True, timeout=30,
        )
        try:
            archive_result = json.loads(r.stdout)
        except (json.JSONDecodeError, Exception):
            archive_result = {"ok": False, "reason": r.stderr.strip() or "pg-archive.py 输出无法解析"}

        if archive_result.get("ok"):
            src = archive_result.get("src", "")
            target = archive_result.get("target", "")
            if src:
                self._git("rm", "-r", "--cached", src)
            if target:
                self._git("add", target)
            commit_r = self._git("commit", "-m", f"archive change {self.change}")
            # 切 event_log path 到 archive 新位置，避免后续 append 写到被 mv 的原路径
            # 产生孤儿文件（archive 后原目录已不存在，append 会创建空文件残留）。
            if target:
                archive_event_path = os.path.join(
                    bootstrap.PROJECT_ROOT, target, "2-build", "pipeline.events"
                )
                self.event_log.update_path(archive_event_path)
            if commit_r.returncode == 0:
                sha = self._git("rev-parse", "HEAD").stdout.strip()
                archive_result["commit"] = {"sha": sha, "committed": True}
                self.event_log.append(EVT_GIT_COMMIT, {
                    "sha": sha, "message": f"archive change {self.change}",
                    "branch": f"feat/pg/{self.change}",
                })
            else:
                archive_result["commit"] = {
                    "committed": False, "reason": commit_r.stderr.strip() or "commit failed",
                }

        return archive_result