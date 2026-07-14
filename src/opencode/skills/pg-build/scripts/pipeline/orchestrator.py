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
    EVT_DISPATCH_ABANDONED,
    EVT_GIT_COMMIT,
    EVT_GAP_ACCEPTED,  # v2.1: fix/gate-fix 循环耗尽后接受的 gap
    STATUS_COMPLETED,  # v2.1: 单一来源替换字面量
    STATUS_PASS,
)
from pipeline.reducer import reduce_state
from pipeline.detect import next_pending
from pipeline.dispatch import build_action, build_final_gate_action
from pipeline.config import (
    load_project_config,
    resolve_module_details,
    resolve_module_roots,
    resolve_test_commands,
    resolve_module_languages,
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


def _derive_result_path(state: PipelineState, track: str, phase: str) -> str:
    """v2.4 新增：派生当前 track/phase 对应的 result JSON 路径。

    实现方式：扫描 2-build/ 找最新匹配的 `<seq>-<track>-<phase>-dispatch[-cycle].md`，
    提取 seq 和 cycle，构造对应的 result.json 路径。

    命名规则（与 dispatch.py 一致）：
      - 普通 phase: <seq>-<track>-<phase>-result.json
      - fix phase 加 cycle: <seq>-<track>-phase-result-<cycle>.json
      - final-gate: <seq>-final-gate-gate-result.json

    Args:
        state: 当前 PipelineState（提供 change name）
        track: 当前 track id
        phase: 当前 phase

    Returns:
        expected_result_path 绝对路径（不含则返回空字符串）
    """
    if not track or not phase or not state.change:
        return ""

    build_dir = os.path.join(_change_root(state.change), "2-build")
    if not os.path.isdir(build_dir):
        return ""

    # 扫描 2-build/ 下匹配 "<seq>-<track>-<phase>-dispatch[-cycle].md" 的最新文件
    if track == FINAL_GATE_TRACK:
        prefix_check = "final-gate-gate-dispatch"
    else:
        prefix_check = f"{track}-{phase}-dispatch"

    matches: list[tuple[int, int, str]] = []  # (seq, cycle, filename)
    seq_re = __import__("re").compile(r"^(\d{3})-(.+)$")
    for fname in os.listdir(build_dir):
        if not fname.endswith(".md"):
            continue
        m = seq_re.match(fname)
        if not m:
            continue
        rest = m.group(2)
        if not rest.startswith(prefix_check):
            continue

        # 提取 cycle (suffix -<n> 在 .md 前)
        body = rest[:-3]  # 去 .md
        cycle = 1
        # body 形如 "<track>-<phase>-dispatch[-<cycle>]"
        if "-" in body:
            parts = body.rsplit("-", 1)
            if parts[1].isdigit():
                cycle = int(parts[1])
        matches.append((int(m.group(1)), cycle, fname))

    if not matches:
        return ""

    # 取最大 seq (优先) + 最大 cycle
    matches.sort(key=lambda x: (x[0], x[1]))
    ds, cycle, _ = matches[-1]

    # 构造 result.json 路径
    if track == FINAL_GATE_TRACK:
        filename = f"{ds:03d}-final-gate-gate-result.json"
    else:
        prefix = f"{ds:03d}-{track}-{phase}-result"
        if cycle > 1:
            prefix += f"-{cycle}"
        filename = f"{prefix}.json"

    return os.path.join(build_dir, filename)


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

        首次调用时检测 pipeline 配置并初始化 state（bootstrap 已由独立命令完成）。
        
        P3: 检查是否有上次 dispatch 尚未 record，若存在则返回 retry action 而非新 dispatch。
        """
        # bootstrap 状态检查
        if not self.state.pipeline_order:
            # 首次调用：检测配置 + 初始化 state（bootstrap 副作用已由 $RUNNER bootstrap 完成）
            return self._first_next()

        # 检查是否已 terminal
        if self.state.status == "completed":
            return {"action": "done", "status": "completed"}
        if self.state.status == "failed":
            return {
                "action": "workflow_failed", "fatal": True,
                "reason": self.state.failed_reason or "unknown",
            }

        # P3: 上次 dispatch 尚未 record → 非新 dispatch，返回 retry
        if self.state.last_dispatch_file and self.state.current_track and self.state.current_phase:
            rc = self.state.retry_count
            if rc >= 3:
                # 超过 3 次重试 → 废弃
                self.event_log.append(EVT_DISPATCH_ABANDONED, {
                    "track": self.state.current_track,
                    "phase": self.state.current_phase,
                    "dispatch_file": self.state.last_dispatch_file,
                    "retry_count": rc,
                })
                self.state = self.state.replace(
                    status="failed",
                    failed_reason=f"dispatch abandoned after {rc} retries: "
                                  f"{self.state.current_track}:{self.state.current_phase}",
                    last_dispatch_file="",
                    retry_count=0,
                )
                save_snapshot(self.change_root, self.state)
                return {
                    "action": "workflow_failed", "fatal": True,
                    "reason": f"dispatch abandoned after {rc} retries",
                }
            self.state = self.state.replace(retry_count=rc + 1)
            save_snapshot(self.change_root, self.state)
            return {
                "action": "retry",
                "dispatch_file": self.state.last_dispatch_file,
                "item": self.state.current_track,
                "sub": self.state.current_phase,
                "retry_count": rc + 1,
                "max_retries": 3,
            }

        # 下一步 dispatch
        action = next_pending(self.state)
        return self._action_to_dict(action)

    def _first_next(self) -> dict[str, Any]:
        """首次 next：检测配置 + 初始化 TrackState（bootstrap 已由独立命令完成）。

        与 bootstrap CLI 命令的分工：
          - bootstrap 命令：migrate / feature branch / init commit / prepare_env
          - _first_next：读取 manifest + project.yaml，创建 TrackState，写 snapshot
        """
        # 找 pipeline_order + track 配置 + stage 配置
        order, track_configs = self._detect_pipeline_config()
        stage_order, stage_env_map = self._detect_stage_config()

        # 读取 project.yaml 富化上下文 + 获取 env timeout
        project_config = load_project_config(bootstrap.PROJECT_ROOT)
        stage_env_timeout: dict[str, int] = {}
        if project_config:
            for env_name, env_cfg in (project_config.get("environments") or {}).items():
                for phase_name in ("prepare_env", "clean_env"):
                    action = env_cfg.get(phase_name)
                    if action and isinstance(action, dict):
                        timeout = action.get("timeout_seconds", 600)
                        if env_name not in stage_env_timeout or timeout > stage_env_timeout[env_name]:
                            stage_env_timeout[env_name] = timeout
                        break

        SUB_PHASE_NAMES = ("test", "dev", "review", "verify", "gate")

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
                max_review_fix_retries=cfg.get("max_review_fix_retries", 3),
                module_roots=resolve_module_roots(project_config, module_names),
                module_details=resolve_module_details(project_config, module_names),
                test_commands=resolve_test_commands(project_config, module_names),
                env_name=env_name,
                env_instances_yaml=resolve_env_instances(project_config, env_name),
                hooks_yaml=resolve_hooks(project_config, env_name),
                prepare_status="ok",
                label=cfg.get("description", bare),
                tasks_by_phase=tasks_by_phase,
                commands=tuple(cfg.get("commands", [])),
                timeout_seconds=cfg.get("timeout_seconds", 1800),
                code_review_enabled=cfg.get("code_review_enabled", True),
                code_review_profiles=tuple(cfg.get("code_review_profiles", ())),
                code_review_profile=cfg.get("code_review_profile", ""),
                code_review_languages=resolve_module_languages(project_config, module_names),
                verify_enabled=cfg.get("verify_enabled", True),
                gate_enabled=cfg.get("gate_enabled", True),
            )
            is_simple = cfg.get("type") == "simple"
            if is_simple:
                track_types[tid] = "simple"
                # v3.x: simple track 自动跳过 review（manifest 不生成 phase_prompts）
                # v3.4: 同时跳过 verify / gate（simple track 不走 TDVG）
                tracks[tid] = tracks[tid].replace(
                    code_review_enabled=False,
                    verify_enabled=False,
                    gate_enabled=False,
                )

        # 确定当前 stage
        first_stage = PipelineState.extract_stage(order[0]) if order else ""

        self.state = self.state.replace(
            pipeline_order=tuple(order),
            tracks=tracks,
            track_types=track_types,
            stage_order=tuple(stage_order),
            stage_env_map=stage_env_map,
            stage_env_timeout=stage_env_timeout,
            current_stage=first_stage,
            status="running",
        )

        # v2.1.1: 不再预设 stage_prepared={first_stage}。
        # 之前 bootstrap 命令同步执行 prepare_env 并把 first_stage 标为已 prepared，
        # 但这与 "bootstrap 不再执行 env hook" 的新协议冲突 —— 现在 first_stage
        # 的 prepare_env 由首次 next() 返回的 env_switch action 触发，编排器按 plan
        # 自己 bash 执行后调 env-action-result 才会把 first_stage 加进 stage_prepared。
        # 这样多 stage 流程的 stage_prepared 状态机才能正确推进。

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
                        "description": cfg.get("description", ""),
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

    def record(
        self,
        status: str,
        report_path: str = "",
        summary: str = "",
        outputs: str = "",
        issues: str = "",
        evidence_paths: list[str] | None = None,  # v2.2
        tasks_updated: list[str] | None = None,  # v2.2
        design_md_fault: bool = False,  # v2.7
        design_md_fault_location: str = "",  # v2.7
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

        # ── v2.4: result JSON 落盘校验 ──
        # 派生 expected_result_path（与 dispatch.py 命名规则一致）
        expected_result_path = _derive_result_path(self.state, track, phase)
        if expected_result_path and not os.path.isfile(expected_result_path):
            # v2.4: 触发 fatal（编排器已重试一次，这里是第二次检查）
            return {
                "action": "error",
                "fatal": True,
                "reason": (
                    f"result_json_missing_after_retry: "
                    f"sub-agent 未生成 {expected_result_path}\n"
                    f"重试一次后仍缺失。sub-agent 必须调用:\n"
                    f"  pg-build-result --output-path {expected_result_path} --require-output ..."
                ),
                "phase": phase,
                "track": track,
                "hint": (
                    "v2.4 保护规则：编排器在 dispatch 后检查 "
                    "<seq>-<track>-<phase>.result.json 是否落盘。\n"
                    "重试后仍缺失 = sub-agent 未执行 pg-build-result 脚本。\n"
                    "修复方法：sub-agent 必须调用:\n"
                    f"  python3 .opencode/skills/pg-build/scripts/pg-build-result "
                    f"--mode agent --output-path {expected_result_path} --require-output "
                    f"--status <status> --summary \"<summary>\" ..."
                ),
            }

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
            evidence_paths=tuple(evidence_paths or []),  # v2.2
            tasks_updated=tuple(tasks_updated or []),  # v2.2
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

        # 构建 record（v2.2: 含 evidence_paths + tasks_updated）
        record = PipelineRecord(
            track=track,
            phase=phase,
            status=status,
            summary=summary,
            report_path=report_path or None,
            issues=issues,
            evidence_paths=tuple(evidence_paths or []),
            tasks_updated=tuple(tasks_updated or []),
            design_md_fault=design_md_fault,  # v2.7
            design_md_fault_location=design_md_fault_location,  # v2.7
        )

        # reducer
        new_state, action = reduce_state(self.state, record)

        # ── v2.3: error path 无副作用保护 ──
        # reducer 可能返回 kind="error"（如 escalate 缺 tasks_updated）。
        # 这种情况下不应写 event_log、不应 save_snapshot、不应 _auto_commit。
        # 否则会破坏持久层（snapshot 被清空、commit 多写）。
        # reducer 已经保留 state 内容，编排器只需要把 error 透传给 caller。
        if action.kind == "error":
            return {
                "action": "error",
                "fatal": False,  # 非 fatal：编排器可以选择重试 record
                "reason": action.detail.get("reason", "unknown"),
                "phase": phase,
                "track": track,
                "hint": (
                    "reducer 拒绝此 record；state 未变，event 未写，commit 未做。"
                    "请修正 record 参数（如 escalate 必传 --tasks-updated）后重试。"
                ),
            }

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

            # [v2.1] 检测 track.accepted_gaps 增量 → 写 EVT_GAP_ACCEPTED 事件
            new_track = new_state.tracks.get(track, TrackState.create(track))
            old_track = self.state.tracks.get(track, TrackState.create(track))
            if len(new_track.accepted_gaps) > len(old_track.accepted_gaps):
                new_gaps = new_track.accepted_gaps[len(old_track.accepted_gaps):]
                for gap in new_gaps:
                    self.event_log.append(EVT_GAP_ACCEPTED, {
                        "track": gap["track"],
                        "phase": gap["phase"],
                        "cycles_attempted": gap["cycles_attempted"],
                        "max_cycles": gap["max_cycles"],
                        "issues": gap["issues"],
                        "accepted_at": gap["accepted_at"],
                    })

        if status == "escalate" and phase == "verify":
            verify = new_state.tracks.get(track, TrackState.create(track)).phases.get("verify", PhaseState())
            cycle = len(verify.fix_cycles)
            self.event_log.append(EVT_FIX_CYCLE_STARTED, {"track": track, "cycle": cycle, "source_report": report_path})

        # v2.3: 移除 fix_skipped_verify 事件（fix 完成后总是 re_verify）

        # 更新 state
        self.state = new_state.replace(
            last_dispatch_file="",  # P3: 清除 stale dispatch 标记
            retry_count=0,
        )
        save_snapshot(self.change_root, new_state)

        # 同步 tasks.md checkbox（Item 2）
        # v2.1: 使用 STATUS_* 常量替代硬编码字面量
        # v2.2+: 优先使用 tasks_updated 做精准标记，空列表时 fallback 到全段标记
        if status in (STATUS_COMPLETED, STATUS_PASS):
            try:
                from pipeline.tasks_md import mark_tasks_by_ids, mark_phase_completed
                if tasks_updated:
                    mark_tasks_by_ids(self.change_root, track, phase, tasks_updated)
                else:
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
            # 更新 state 的 current_track/current_phase/last_dispatch_file
            self.state = self.state.replace(
                current_track=action.track,
                current_phase=action.phase,
                last_dispatch_file=result.get("dispatch_file", ""),
                retry_count=0,
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
            # env_switch 透传给编排器。编排器收到后应调
            # $RUNNER env-action <change> --phase <phase> --stage <stage> --env <env>
            # 执行完成后调 next() 继续。
            return {
                "action": "env_switch",
                "phase": action.phase,
                "stage": action.detail.get("stage", ""),
                "env_name": action.detail.get("env_name", ""),
                "next_stage": action.detail.get("next_stage", ""),
                "next_env_name": action.detail.get("next_env_name", ""),
                "hook_timeout_seconds": action.detail.get("hook_timeout_seconds", 600),
            }

        return {"action": action.kind, **action.detail}

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

        v2.7: 优先信任 snapshot.phases.gate.report_path 字段；
        仅当该字段为空或指向不存在的文件时，退化到 glob 扫描。
        glob 仅匹配 -gate.md 一种命名（统一命名约定）。

        v2.1: 原始实现（完全依赖 glob 扫描，匹配多种命名）。

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

            # ── v2.7: 优先信任 snapshot 中已记录的 report_path ──
            gate_phase = t.phases.get("gate", PhaseState())
            report_path = gate_phase.report_path
            if report_path and os.path.isfile(report_path):
                continue

            # ── 退化：glob 扫描（仅 -gate.md 一种命名）──
            track_bare = tid.rsplit(".", 1)[-1]
            candidates = [
                f for f in existing
                if f.endswith(".md")
                and (
                    f.endswith(f"-{track_bare}-gate.md")
                    or f.endswith(f"-{tid}-gate.md")
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