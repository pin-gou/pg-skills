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
)
from pipeline.reducer import reduce_state
from pipeline.detect import next_pending
from pipeline.sub_pipeline import FIX_CYCLE, GATE_FIX_CYCLE
from pipeline.dispatch import build_action, build_final_gate_action
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
    """

    def __init__(self, change: str):
        self.change = change
        self.change_root = _change_root(change)
        self.event_log = EventLog(change_root=self.change_root)
        self.state: PipelineState = load_snapshot(self.change_root) or PipelineState(change=change)

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
            }

        # 标记 init_committed
        if boot_result.get("init_commit") and boot_result["init_commit"].get("committed"):
            self.state = self.state.replace(init_committed=True)

        # 找 pipeline_order + track 配置
        order, track_configs = self._detect_pipeline_config()
        tracks: dict[str, TrackState] = {}
        track_types: dict[str, str] = {}
        for tid in order:
            if tid == FINAL_GATE_TRACK:
                continue
            cfg = track_configs.get(tid, {})
            tracks[tid] = TrackState.create(
                tid,
                modules=tuple(cfg.get("modules", [])),
                max_fail_retries=cfg.get("max_fail_retries", 3),
                max_fix_retries=cfg.get("max_fix_retries", 5),
                max_gate_fix_retries=cfg.get("max_gate_fix_retries", 2),
            )
            if cfg.get("type") == "simple":
                track_types[tid] = "simple"

        self.state = self.state.replace(
            pipeline_order=tuple(order),
            tracks=tracks,
            track_types=track_types,
            status="running",
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
                    }
            except Exception:
                pass

        return order, track_configs

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
        """
        if action.kind == "dispatch":
            is_final = action.track == FINAL_GATE_TRACK
            if is_final:
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
            result = {"action": "done", "status": "completed"}
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

        return {"action": action.kind, **action.detail}

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """执行 git 命令（在项目根目录）。"""
        return subprocess.run(
            ["git", *args],
            capture_output=True, text=True,
            cwd=bootstrap.PROJECT_ROOT,
        )

    def _auto_commit(self, status: str, track: str, phase: str) -> dict[str, Any]:
        """record 后自动 git commit（仅在 pipeline 运行时执行）。"""
        porcelain = self._git("status", "--porcelain").stdout.strip()
        if not porcelain:
            return {
                "attempted": True, "committed": False, "reason": "工作区干净，无可提交内容",
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