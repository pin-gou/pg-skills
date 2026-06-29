#!/usr/bin/env python3
"""pg_pipeline_state_v2.py — PipelineState: SSOT for runner execution state.

Replaces both v1 state.json AND the tasks.md checkbox semantics used for
state inference. tasks.md checkboxes are now strictly a derived view
(rendered from this state on demand, written back as a side-effect of
state transitions).

State schema (v2):

  .pg/changes/<change>/2-build/.pipeline-state.json
  {
    "version": 2,
    "schema_version": "2026-06-29",
    "change": "<change>",
    "stages":         [Stage, ...],         # stage status (pending|in_progress|completed|skipped)
    "tracks":         {track_id: Track},    # per-track state, see Track
    "current_dispatch": Dispatch | null,    # in-flight dispatch (idempotent resume)
    "dispatch_history": [DispatchEntry],    # append-only history (SSOT, replaces manifest)
    "context":        Context              # init/feature/pipeline_order/completed/failed
  }

Each Track is keyed by qualified track_id (e.g. "dev.backend") and contains:

  {
    "track_id":    "dev.backend",
    "bare":        "backend",
    "label":       "<description>",
    "status":      "pending|running|completed|failed|skipped",
    "modules":     [...],
    "config_snapshot": {...},
    "phases": {
      "test":   Phase,
      "dev":    Phase,
      "verify": Phase,                  # includes cycles[] + fix_cycles[]
      "gate":   Phase,                  # includes gate_cycles[] + fix_gates[]
      "fix":    Phase | None,           # verify-fix sub-loop (flat sibling, not nested)
      "fix-gate": Phase | None,         # gate-fix sub-loop
      "simple": Phase | None,           # simple track
    }
  }

A Phase looks like:

  {
    "status":        "pending|running|completed|failed",
    "attempt":       1,
    "started_at":    ISO8601,
    "completed_at":  ISO8601 | null,
    "agent":         "pg-build/test",
    "result":        {kind: "completed"|"escalate"|"failed"|"pass"|"fail", summary: "..."},
    "tasks_marked":  [int, ...],         # task IDs marked complete in tasks.md
    "report_path":   "<path>" | null,
    # phase-specific fields (verify, gate) are described in the plan §2.3.
  }

Public API (frozen for the duration of build-r; signatures may only change
when accompanied by a test update):

  PipelineState(change)                  — load (or init empty v2) state
  next_pending() -> NextDispatch | None  — decide next dispatch
  record_dispatch_started(...)           — mark dispatch in-flight
  record_completed(...)                  — mark phase complete, advance state
  record_escalate(...)                   — verify→fix transition
  record_fix_completed(...)              — fix→re-verify transition
  record_pass(...)                       — gate→track complete
  record_fail(...)                       — gate→fix-gate or exhausted
  record_gate_exhausted(...)             # accepted gaps, track completed
  record_task_marked(...)                # CLI-driven task marking
  render_tasks_checkboxes() -> str       # derived tasks.md view
  commit()                               # atomic write

Backward compatibility: v1 state.json files are accepted as input via
PipelineState.from_v1_state(v1_dict, change). v1 coexists during Steps 1-2
for shadow validation; main-path switch happens in Step 3.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


SCHEMA_VERSION = "2026-06-29"


# =============================================================================
# Paths (resolve project root the same way the runner does)
# =============================================================================

def _find_project_root(start: str) -> str:
    """Walk up from start looking for .pg/project.yaml.

    Mirrors the runner's find_project_root: the v2 module is meant to live
    under .opencode/skills/pg-build/scripts/, so we look for .pg/ next to
    the project that imported us.
    """
    cur = os.path.abspath(start)
    for _ in range(8):
        if os.path.isfile(os.path.join(cur, ".pg", "project.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise FileNotFoundError(
        "pg_pipeline_state_v2: could not find project root with .pg/project.yaml"
    )


def _now_iso() -> str:
    """Return current local time as ISO8601 with offset (matches runner convention)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# =============================================================================
# Data classes (immutable views over state)
# =============================================================================

@dataclass(frozen=True)
class NextDispatch:
    """Decision returned by PipelineState.next_pending()."""
    track: str          # qualified track id (e.g. "dev.backend")
    phase: str          # "test" | "dev" | "verify" | "gate" | "fix" | "fix-gate" | "simple"
    cycle: int          # verify/gate cycle number (1-based; 1 for non-cycling phases)
    agent: str          # "pg-build/test" | etc.
    kind: str           # "dispatch" | "dispatch_fix" | "dispatch_final_gate"
    is_resume: bool = False  # True when this is the same as current_dispatch


# Sub-agent mapping (mirrors runner's SUB_AGENTS)
PHASE_AGENTS = {
    "test":     "pg-build/test",
    "dev":      "pg-build/dev",
    "verify":   "pg-build/verify",
    "gate":     "pg-build/gate",
    "fix":      "pg-build/fix",
    "fix-gate": "pg-build/fix-gate",
    "simple":   "pg-build/simple",
}

# SUB_PHASES ordering — same as runner's `SUB_PHASES` minus 'simple'.
TDVG_PHASES = ["test", "dev", "verify", "gate"]


# =============================================================================
# PipelineState — the class itself
# =============================================================================

class PipelineState:
    """SSOT for runner execution state (v2 schema).

    All mutation goes through the `record_*` methods. Read access via
    properties / `data` dict for serialization. Use `commit()` to persist
    atomically (write to .tmp, then rename).
    """

    # ── Construction / persistence ──────────────────────────────────

    def __init__(self, change: str, project_root: Optional[str] = None):
        self.change = change
        self.project_root = project_root or _find_project_root(
            os.path.dirname(os.path.abspath(__file__))
        )
        self.changes_dir = os.path.join(self.project_root, ".pg", "changes")
        self.apply_dir = os.path.join(self.changes_dir, change, "2-build")
        self.state_path = os.path.join(self.apply_dir, ".pipeline-state.json")
        self._data = self._load_or_init()
        self._dirty = False

    def _load_or_init(self) -> dict:
        if os.path.isfile(self.state_path):
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        return self._empty_state()

    def _empty_state(self) -> dict:
        return {
            "version": 2,
            "schema_version": SCHEMA_VERSION,
            "change": self.change,
            "stages": [],
            "tracks": {},
            "current_dispatch": None,
            "dispatch_history": [],
            "context": {
                "init_committed": False,
                "init_commit_sha": None,
                "feature_branch": None,
                "pipeline_order": [],
                "current_stage_idx": 0,
                "completed": False,
                "failed": False,
                "failed_reason": None,
            },
        }

    @property
    def data(self) -> dict:
        """Raw state dict (read-only externally — mutate via record_* methods)."""
        return self._data

    def commit(self) -> None:
        """Atomically persist state: write to .tmp then rename."""
        os.makedirs(self.apply_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".pipeline-state.", suffix=".tmp",
            dir=self.apply_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
            self._dirty = False
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

    # ── v1 → v2 migration (for Step 4 / archive replay) ────────────

    @classmethod
    def from_v1_state(cls, v1: dict, change: str, project_root: Optional[str] = None) -> "PipelineState":
        """Construct a v2 PipelineState from an in-memory v1 state dict.

        Used by:
          - Step 3 archive replay tests (read v1 manifest, rebuild v2)
          - Step 4 migrate_v1_to_v2.py (one-shot in-flight change migration)

        Translation is deliberately lossy: v1 has no fix_cycles[] history,
        so we seed with what we can infer from `current` + `completed_items`.
        """
        ps = cls.__new__(cls)
        ps.change = change
        ps.project_root = project_root or _find_project_root(
            os.path.dirname(os.path.abspath(__file__))
        )
        ps.changes_dir = os.path.join(ps.project_root, ".pg", "changes")
        ps.apply_dir = os.path.join(ps.changes_dir, change, "2-build")
        ps.state_path = os.path.join(ps.apply_dir, ".pipeline-state.json")
        ps._dirty = False
        ps._data = cls._translate_v1_to_v2(v1, change)
        return ps

    @staticmethod
    def _translate_v1_to_v2(v1: dict, change: str) -> dict:
        """v1 → v2 translation. Best-effort; missing data gets sensible defaults."""
        v2 = {
            "version": 2,
            "schema_version": SCHEMA_VERSION,
            "change": change,
            "stages": [],
            "tracks": {},
            "current_dispatch": None,
            "dispatch_history": [],
            "context": {
                "init_committed": v1.get("init_committed", False),
                "init_commit_sha": v1.get("init_commit_sha"),
                "feature_branch": v1.get("feature_branch"),
                "pipeline_order": v1.get("pipeline_order", []),
                "current_stage_idx": 0,
                "completed": v1.get("completed", False),
                "failed": v1.get("failed", False),
                "failed_reason": v1.get("fail_reason"),
            },
        }

        # current → current_dispatch
        cur = v1.get("current")
        if cur:
            v2["current_dispatch"] = {
                "track": cur.get("item"),
                "phase": cur.get("sub"),
                "cycle": cur.get("fix_cycles", cur.get("gate_cycles", 0)) + 1,
                "attempt": cur.get("attempt", 1),
                "agent": PHASE_AGENTS.get(cur.get("sub", ""), ""),
                "started_at": cur.get("started_at"),
                "waiting": cur.get("waiting", False),
                "report_path": None,
                "result_received": False,
            }
            # in_fix_cycle determines whether sub is actually fix/fix-gate
            if cur.get("in_fix_cycle"):
                v2["current_dispatch"]["phase"] = cur.get("sub")

        # completed_items → tracks[track].status = completed (best-effort)
        for tid in v1.get("completed_items", []) or []:
            bare = tid.rsplit(".", 1)[-1] if "." in tid else tid
            v2["tracks"].setdefault(tid, {
                "track_id": tid,
                "bare": bare,
                "label": None,
                "status": "completed",
                "modules": [],
                "config_snapshot": {},
                "phases": {},
            })
        return v2

    # ── Read API ───────────────────────────────────────────────────

    def is_track_completed(self, track: str) -> bool:
        return self._data["tracks"].get(track, {}).get("status") == "completed"

    def has_open_phase(self, track: str, phase: str) -> bool:
        return (
            self._data["tracks"]
            .get(track, {})
            .get("phases", {})
            .get(phase, {})
            .get("status") in ("pending", "running")
        )

    def get_fix_cycles(self, track: str, parent_phase: str) -> list:
        """Return fix_cycles[] for verify or fix_gates[] for gate."""
        phase = (
            self._data["tracks"]
            .get(track, {})
            .get("phases", {})
            .get(parent_phase, {})
        )
        if parent_phase == "verify":
            return phase.get("fix_cycles", [])
        if parent_phase == "gate":
            return phase.get("fix_gates", [])
        return []

    def get_phase(self, track: str, phase: str) -> dict:
        """Return the phase dict (creates a pending stub if missing)."""
        t = self._data["tracks"].setdefault(track, {
            "track_id": track,
            "bare": track.rsplit(".", 1)[-1] if "." in track else track,
            "label": None,
            "status": "pending",
            "modules": [],
            "config_snapshot": {},
            "phases": {},
        })
        return t["phases"].setdefault(phase, {
            "status": "pending",
            "attempt": 0,
        })

    def next_pending(self) -> Optional[NextDispatch]:
        """Decide the next dispatch to issue.

        Walks pipeline_order from context, then within each track walks
        TDVG phases (test → dev → verify → gate). Within verify, cycles
        through cycles[]; fix_cycles[] are dispatched as "fix" sub-phase.
        Within gate, cycles through gate_cycles[]; exhausted + accepted
        gaps → track completed.

        Returns:
          None                              — all tracks done (call final-gate)
          NextDispatch(kind="dispatch")     — normal TDVG dispatch
          NextDispatch(kind="dispatch_fix") — verify-fix or gate-fix dispatch
          NextDispatch(kind="dispatch_final_gate") — enter final-gate
          NextDispatch(..., is_resume=True) — same as current_dispatch (idempotent)
        """
        # 1. Idempotent resume: if current_dispatch is waiting, return it.
        cd = self._data.get("current_dispatch")
        if cd and cd.get("waiting"):
            return NextDispatch(
                track=cd["track"],
                phase=cd["phase"],
                cycle=cd.get("cycle", 1),
                agent=cd.get("agent", PHASE_AGENTS.get(cd["phase"], "")),
                kind="dispatch" if cd["phase"] in TDVG_PHASES or cd["phase"] == "simple"
                else "dispatch_fix",
                is_resume=True,
            )

        # 2. Terminal?
        if self._data["context"].get("completed"):
            return NextDispatch(
                track="final-gate",
                phase="gate",
                cycle=1,
                agent="pg-build/gate",
                kind="dispatch_final_gate",
            )
        if self._data["context"].get("failed"):
            return None  # caller surfaces workflow_failed

        # 3. Walk pipeline_order
        order = self._data["context"].get("pipeline_order") or []
        if not order:
            return None  # nothing configured

        for track in order:
            if self.is_track_completed(track):
                continue

            # Initialize track skeleton if missing
            self.get_phase(track, "test")  # ensures track dict exists

            # Determine next phase within track
            nd = self._next_phase_in_track(track)
            if nd is not None:
                return nd

        # 4. All tracks done — enter final-gate
        return NextDispatch(
            track="final-gate",
            phase="gate",
            cycle=1,
            agent="pg-build/gate",
            kind="dispatch_final_gate",
        )

    def _next_phase_in_track(self, track: str) -> Optional[NextDispatch]:
        """Find the next dispatchable phase within `track`."""
        phases = self._data["tracks"][track]["phases"]

        # If there's an open verify-fix (phases["fix"] running), return it.
        if phases.get("fix", {}).get("status") == "running":
            cycle = len(phases["verify"].get("fix_cycles", []))
            return NextDispatch(
                track=track, phase="fix", cycle=cycle,
                agent=PHASE_AGENTS["fix"], kind="dispatch_fix",
            )
        # If there's an open gate-fix (phases["fix-gate"] running), return it.
        if phases.get("fix-gate", {}).get("status") == "running":
            cycle = len(phases["gate"].get("fix_gates", []))
            return NextDispatch(
                track=track, phase="fix-gate", cycle=cycle,
                agent=PHASE_AGENTS["fix-gate"], kind="dispatch_fix",
            )

        # Walk TDVG. If a fix cycle is pending (verify was escalated, or
        # gate failed), dispatch the fix first.
        if phases.get("fix", {}).get("status") in ("pending", "running"):
            cycle = len(phases.get("verify", {}).get("fix_cycles", [])) or 1
            return NextDispatch(
                track=track, phase="fix", cycle=cycle,
                agent=PHASE_AGENTS["fix"], kind="dispatch_fix",
            )
        if phases.get("fix-gate", {}).get("status") in ("pending", "running"):
            cycle = len(phases.get("gate", {}).get("fix_gates", [])) or 1
            return NextDispatch(
                track=track, phase="fix-gate", cycle=cycle,
                agent=PHASE_AGENTS["fix-gate"], kind="dispatch_fix",
            )

        # Walk TDVG
        for phase_name in TDVG_PHASES:
            ph = phases.get(phase_name)
            if ph is None:
                # First-time entry into this phase
                return NextDispatch(
                    track=track, phase=phase_name, cycle=1,
                    agent=PHASE_AGENTS[phase_name], kind="dispatch",
                )
            status = ph.get("status")
            if status == "completed":
                continue
            if status == "running":
                # Resume in-progress phase (e.g. after crash before record)
                cycle = ph.get("current_cycle", 1) if phase_name == "verify" else 1
                return NextDispatch(
                    track=track, phase=phase_name, cycle=cycle,
                    agent=PHASE_AGENTS[phase_name], kind="dispatch",
                    is_resume=True,
                )
            # status == "pending" — first attempt
            if phase_name == "verify":
                ph.setdefault("current_cycle", 1)
            return NextDispatch(
                track=track, phase=phase_name, cycle=1,
                agent=PHASE_AGENTS[phase_name], kind="dispatch",
            )

        # All TDVG phases completed for this track → mark track completed
        self._data["tracks"][track]["status"] = "completed"
        self._data["tracks"][track]["completed_at"] = _now_iso()
        return None  # caller continues to next track in pipeline_order

    # ── Write API ──────────────────────────────────────────────────

    def init_track(self, track: str, label: str = None,
                   modules: list = None, config_snapshot: dict = None) -> None:
        """Initialize a track skeleton (called when first seen in pipeline_order)."""
        if track in self._data["tracks"]:
            return  # already initialized
        bare = track.rsplit(".", 1)[-1] if "." in track else track
        self._data["tracks"][track] = {
            "track_id": track,
            "bare": bare,
            "label": label,
            "status": "pending",
            "started_at": _now_iso(),
            "completed_at": None,
            "modules": modules or [],
            "config_snapshot": config_snapshot or {},
            "phases": {},
        }

    def set_pipeline_order(self, order: list) -> None:
        """Set the pipeline_order in context (called once at first dispatch)."""
        self._data["context"]["pipeline_order"] = list(order)
        self._dirty = True

    def record_dispatch_started(self, track: str, phase: str, agent: str,
                                report_path: str = None) -> dict:
        """Mark a dispatch as started. Transitions phase to 'running'.

        Returns the dispatch_history entry (with auto-assigned seq).
        """
        phase_data = self.get_phase(track, phase)
        phase_data["status"] = "running"
        phase_data["attempt"] = phase_data.get("attempt", 0) + 1
        phase_data["started_at"] = _now_iso()
        phase_data["agent"] = agent

        seq = self._next_seq()
        entry = {
            "seq": seq,
            "track": track,
            "phase": phase,
            "agent": agent,
            "started_at": phase_data["started_at"],
            "result": "pending",
            "dispatch_file": report_path,
        }
        self._data["dispatch_history"].append(entry)

        self._data["current_dispatch"] = {
            "seq": seq,
            "track": track,
            "phase": phase,
            "cycle": phase_data.get("current_cycle", 1) if phase == "verify" else 1,
            "agent": agent,
            "started_at": phase_data["started_at"],
            "waiting": True,
            "report_path": report_path,
            "result_received": False,
        }

        # Update track skeleton
        track_data = self._data["tracks"][track]
        if track_data.get("status") == "pending":
            track_data["status"] = "running"

        self._dirty = True
        return entry

    def record_completed(self, track: str, phase: str, summary: str = "",
                         report_path: str = None,
                         tasks_marked: list = None) -> None:
        """Mark a phase as completed; advance state machine.

        For test/dev/simple: just mark complete (next_pending will walk on).
        For verify: also clears current_dispatch so next_pending advances.
        For gate: equivalent to record_pass (kept distinct for API stability).
        For fix/fix-gate: caller should use record_fix_completed instead.
        """
        phase_data = self.get_phase(track, phase)
        phase_data["status"] = "completed"
        phase_data["completed_at"] = _now_iso()
        phase_data["result"] = {"kind": "completed", "summary": summary}
        if report_path:
            phase_data["report_path"] = report_path
        if tasks_marked:
            phase_data["tasks_marked"] = list(tasks_marked)

        self._close_current_dispatch(track, phase, result_kind="completed")
        self._dirty = True

    def record_escalate(self, track: str, summary: str = "",
                        report_path: str = None) -> int:
        """Verify requests fix cycle. Returns the new fix_cycle number.

        Appends a verify cycle (status=escalate), opens a fix phase.
        """
        verify = self.get_phase(track, "verify")
        cycle_n = len(verify.get("cycles", [])) + 1
        verify.setdefault("cycles", []).append({
            "cycle": cycle_n,
            "status": "escalate",
            "attempt": verify.get("attempt", 1),
            "started_at": verify.get("started_at"),
            "completed_at": _now_iso(),
            "report_path": report_path,
            "issue_summary": summary,
        })
        verify["current_cycle"] = cycle_n

        # Open fix phase
        fix_n = len(verify.get("fix_cycles", [])) + 1
        verify.setdefault("fix_cycles", []).append({
            "cycle": fix_n,
            "sub": "fix",
            "agent": PHASE_AGENTS["fix"],
            "status": "pending",
            "started_at": _now_iso(),
            "report_path": None,
            "fixed_tasks": [],
        })
        self.get_phase(track, "fix")["status"] = "pending"
        self._data["tracks"][track]["phases"]["fix"]["status"] = "pending"
        # Note: fix phase is dispatched via next_pending() because it sees
        # phases["fix"] with status pending — but our TDVG walker doesn't
        # visit fix. Special handling: mark current_dispatch as no longer
        # waiting so next_pending() returns the fix dispatch.
        self._close_current_dispatch(track, "verify", result_kind="escalate")
        self._dirty = True
        return fix_n

    def record_fix_completed(self, track: str, parent_phase: str,
                             summary: str = "",
                             fixed_tasks: list = None) -> None:
        """Mark a fix (verify-fix or gate-fix) cycle as completed.

        Re-opens the parent phase (verify or gate) so next_pending() will
        dispatch the next verify/gate cycle.
        """
        fix_phase_name = "fix" if parent_phase == "verify" else "fix-gate"
        parent = self.get_phase(track, parent_phase)

        fix_phase = self.get_phase(track, fix_phase_name)
        fix_phase["status"] = "completed"
        fix_phase["completed_at"] = _now_iso()
        fix_phase["result"] = {"kind": "completed", "summary": summary}
        if fixed_tasks:
            fix_phase["tasks_marked"] = list(fixed_tasks)

        # Update the fix_cycles[] / fix_gates[] entry
        if parent_phase == "verify":
            fix_list = parent.get("fix_cycles", [])
            if fix_list:
                fix_list[-1]["status"] = "completed"
                fix_list[-1]["completed_at"] = _now_iso()
                if fixed_tasks:
                    fix_list[-1]["fixed_tasks"] = list(fixed_tasks)
        else:
            fix_list = parent.get("fix_gates", [])
            if fix_list:
                fix_list[-1]["status"] = "completed"
                fix_list[-1]["completed_at"] = _now_iso()

        # Re-open parent so next_pending dispatches next cycle
        parent["status"] = "pending"
        parent["attempt"] = parent.get("attempt", 1)  # don't bump attempt for re-verify
        parent["started_at"] = None
        parent["completed_at"] = None

        self._close_current_dispatch(track, fix_phase_name, result_kind="completed")
        self._dirty = True

    def record_pass(self, track: str, summary: str = "",
                    report_path: str = None) -> None:
        """Gate passed → track marked completed."""
        if track == "final-gate":
            self._data["context"]["completed"] = True
            self._data["context"]["completed_at"] = _now_iso()
            self._data["current_dispatch"] = None
            self._dirty = True
            return

        gate = self.get_phase(track, "gate")
        gate["status"] = "pass"
        gate["completed_at"] = _now_iso()
        gate["result"] = {"kind": "pass", "summary": summary}
        if report_path:
            gate["report_path"] = report_path
        gate.setdefault("gate_cycles", []).append({
            "cycle": len(gate.get("gate_cycles", [])) + 1,
            "status": "pass",
            "report_path": report_path,
            "at": _now_iso(),
        })

        self._data["tracks"][track]["status"] = "completed"
        self._data["tracks"][track]["completed_at"] = _now_iso()
        self._data["current_dispatch"] = None
        self._dirty = True

    def record_fail(self, track: str, summary: str = "",
                    report_path: str = None,
                    fixed_tasks: list = None) -> int:
        """Gate failed → enter gate-fix cycle (unless exhausted).

        Returns the new gate_cycle number.
        """
        gate = self.get_phase(track, "gate")
        gate_n = len(gate.get("gate_cycles", [])) + 1
        gate.setdefault("gate_cycles", []).append({
            "cycle": gate_n,
            "status": "fail",
            "report_path": report_path,
            "fixed_tasks": list(fixed_tasks or []),
            "at": _now_iso(),
        })
        gate["gate_cycles_count"] = gate_n
        gate["status"] = "pending"  # waiting for fix-gate → re-gate
        gate["result"] = {"kind": "fail", "summary": summary}

        # Open fix-gate phase
        self.get_phase(track, "fix-gate")["status"] = "pending"
        self._close_current_dispatch(track, "gate", result_kind="fail")
        self._dirty = True
        return gate_n

    def record_gate_exhausted(self, track: str, accepted_gaps: list,
                              report_path: str = None) -> None:
        """Gate-fix exhausted → track completed with accepted gaps."""
        gate = self.get_phase(track, "gate")
        gate["status"] = "pass"  # decision 2: exhausted = PASS with known issues
        gate["accepted_gaps"] = list(accepted_gaps)
        gate["completed_at"] = _now_iso()

        self._data["tracks"][track]["status"] = "completed"
        self._data["tracks"][track]["completed_at"] = _now_iso()
        self._data["tracks"][track]["accepted_gaps"] = list(accepted_gaps)
        self._data["current_dispatch"] = None
        self._dirty = True

    def record_failed(self, track: str, phase: str, attempt: int,
                      error: str) -> None:
        """Bump phase attempt; if exhausted the runner should call
        context.fail() to terminate the workflow."""
        phase_data = self.get_phase(track, phase)
        phase_data["attempt"] = attempt
        phase_data["last_error"] = error
        phase_data["status"] = "pending"  # ready for retry
        self._dirty = True

    def record_task_marked(self, track: str, phase: str, task_id: int) -> None:
        """CLI-driven task marking (Step 5). Appends to phase.tasks_marked.

        Does NOT mutate tasks.md; that side-effect is the caller's job.
        """
        phase_data = self.get_phase(track, phase)
        marked = phase_data.setdefault("tasks_marked", [])
        if task_id not in marked:
            marked.append(task_id)
        self._dirty = True

    def mark_workflow_failed(self, reason: str) -> None:
        """Terminal: workflow_failed."""
        self._data["context"]["failed"] = True
        self._data["context"]["failed_reason"] = reason
        self._data["current_dispatch"] = None
        self._dirty = True

    # ── Render (tasks.md derived view) ──────────────────────────────

    def render_tasks_checkboxes(self) -> str:
        """Render a derived tasks.md view from current state.

        Not the source of truth — this is for human audit / archival.
        Step 5 will make tasks.md strictly a derived view written back
        via the mark-task CLI.
        """
        lines = [f"# {self.change} Tasks (derived from state.json v2)\n"]
        for track_id, t in self._data["tracks"].items():
            lines.append(f"\n## {track_id}  ({t.get('label', '')})\n")
            for phase_name in TDVG_PHASES:
                ph = t.get("phases", {}).get(phase_name)
                if not ph:
                    continue
                status = ph.get("status", "pending")
                marked = ph.get("tasks_marked", [])
                lines.append(f"\n### {phase_name}  status={status}\n")
                for tid in marked:
                    lines.append(f"- [x] {track_id}:{phase_name} 任务 {tid}\n")
                # Unmarked tasks would need tasks.md as input; we render
                # only what we know from state.
        return "".join(lines) + "\n"

    # ── Internals ──────────────────────────────────────────────────

    def _next_seq(self) -> str:
        """Allocate next dispatch seq (3-digit zero-pad)."""
        if not self._data["dispatch_history"]:
            return "001"
        last = self._data["dispatch_history"][-1]["seq"]
        try:
            return f"{int(last) + 1:03d}"
        except (ValueError, TypeError):
            return f"{len(self._data['dispatch_history']) + 1:03d}"

    def _close_current_dispatch(self, track: str, phase: str,
                                result_kind: str) -> None:
        """Update current_dispatch bookkeeping; clear if matches."""
        cd = self._data.get("current_dispatch")
        if cd and cd.get("track") == track and cd.get("phase") == phase:
            cd["waiting"] = False
            cd["result_received"] = True
            cd["result_kind"] = result_kind
            # Don't clear yet — next_pending() will resume OR advance.
            # Clearing here breaks idempotent resume; only clear on advance.
            # Clear ONLY when next_pending will move to a different (track,phase).
        # Update last dispatch_history entry result
        if self._data["dispatch_history"]:
            self._data["dispatch_history"][-1]["result"] = result_kind
            self._data["dispatch_history"][-1]["result_at"] = _now_iso()


# =============================================================================
# Convenience: load + main CLI for debug
# =============================================================================

USAGE = """Usage:
  pg_pipeline_state_v2.py <change> [--show]                 # dump state.json
  pg_pipeline_state_v2.py <change> --next                  # show next pending dispatch
  pg_pipeline_state_v2.py <change> mark-task <track> <phase> <task_id>
                                                          # CLI-driven task marking (Step 5)
  pg_pipeline_state_v2.py <change> render-tasks-md         # render derived tasks.md

mark-task writes:
  - state.json: phases.<phase>.tasks_marked appends <task_id>
  - tasks.md:   the matching `- [ ] X.Y` becomes `- [x] X.Y` (write-through)
The state.json is the SSOT; tasks.md is a derived view from Step 5 onwards.
"""


def _find_cwd_project_root() -> str:
    """Walk up from CWD looking for .pg/project.yaml (used when CLI is
    invoked from a consumer project, not from the scripts/ directory).
    """
    cur = os.path.abspath(os.getcwd())
    for _ in range(8):
        if os.path.isfile(os.path.join(cur, ".pg", "project.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise FileNotFoundError(
        f"pg_pipeline_state_v2: no .pg/project.yaml found above {os.getcwd()}"
    )


def _find_tasks_md_path(change: str, project_root: str) -> str:
    """Return tasks.md path for a change (used by mark-task write-through)."""
    return os.path.join(project_root, ".pg", "changes", change, "tasks.md")


def _write_through_tasks_md(change: str, project_root: str,
                              track: str, phase: str, task_id: int) -> bool:
    """Update tasks.md checkbox for the matching X.Y task. Returns True if
    a line was changed.

    The line must look like `- [ ] X.Y <description>` and belong to the
    section matching `track:phase` (e.g. `## 3. dev.backend:verify`).

    Bails out silently (returns False) if tasks.md does not exist or the
    task / section is not found. The state.json write is the SSOT — this
    is just a derived view kept in sync for human audit.
    """
    import re

    tasks_path = _find_tasks_md_path(change, project_root)
    if not os.path.isfile(tasks_path):
        return False

    with open(tasks_path, encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines(keepends=True)

    # Find the section heading for (track, phase). Section heading pattern:
    #   ## N. <track>:<phase> - <label>
    heading_pat = re.compile(
        rf"^##\s+\d+\.\s+{re.escape(track)}:{re.escape(phase)}\b",
        re.MULTILINE,
    )
    m = heading_pat.search(content)
    if not m:
        return False

    # Find the next `## ` or `---` line to bound the section
    start = m.end()
    end = len(content)
    next_heading = re.search(r"^##\s+", content[start:], re.MULTILINE)
    if next_heading:
        end = start + next_heading.start()
    section_text = content[start:end]

    # Within section, find `- [ ] X.Y` and pick the line whose Y == task_id.
    # Note: X is the section number (which we don't care about — the heading
    # already matched track:phase); Y is the sub-task index, which is what
    # mark-task's `task_id` argument refers to.
    task_pat = re.compile(
        r"^(\s*)-\s*\[\s\]\s*(\d+)\.(\d+)(.*)$",
        re.MULTILINE,
    )
    tm = None
    for candidate in task_pat.finditer(section_text):
        if int(candidate.group(3)) == task_id:
            tm = candidate
            break
    if tm is None:
        return False

    # Compute byte offsets in the full file content
    line_start_in_file = start + tm.start()
    line_end_in_file = start + tm.end()

    # Replace `[ ]` with `[x]` in the matched substring
    original_line = content[line_start_in_file:line_end_in_file]
    new_line = original_line.replace("[ ]", "[x]", 1)
    if new_line == original_line:
        return False

    new_content = content[:line_start_in_file] + new_line + content[line_end_in_file:]
    if new_content == content:
        return False

    with open(tasks_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def _cmd_mark_task(argv: list) -> int:
    """CLI: pg_pipeline_state_v2.py <change> mark-task <track> <phase> <task_id>

    Writes state.json (SSOT) and tasks.md (derived view).
    """
    # argv after the `mark-task` token: <track> <phase> <task_id>
    if len(argv) < 3:
        print("Usage: pg_pipeline_state_v2.py <change> mark-task "
              "<track> <phase> <task_id>", file=sys.stderr)
        return 2
    track, phase, task_id_s = argv[0], argv[1], argv[2]
    try:
        task_id = int(task_id_s)
    except ValueError:
        print(f"mark-task: task_id must be integer, got {task_id_s!r}",
              file=sys.stderr)
        return 2

    project_root = _find_cwd_project_root()
    change = _RESOLVED_CHANGE
    if change is None:
        print("internal: _RESOLVED_CHANGE not set", file=sys.stderr)
        return 2
    ps = PipelineState(change=change, project_root=project_root)
    ps.record_task_marked(track=track, phase=phase, task_id=task_id)
    ps.commit()

    wrote_tasks_md = _write_through_tasks_md(
        change=change,
        project_root=project_root,
        track=track, phase=phase, task_id=task_id,
    )

    print(json.dumps({
        "ok": True,
        "track": track,
        "phase": phase,
        "task_id": task_id,
        "tasks_marked": ps.data["tracks"].get(track, {})
                          .get("phases", {}).get(phase, {})
                          .get("tasks_marked", []),
        "tasks_md_updated": wrote_tasks_md,
    }, ensure_ascii=False, indent=2))
    return 0


# Cache the change name across argv-walking helpers
_RESOLVED_CHANGE = None


def _main():
    """Tiny CLI dispatcher.

    Supports:
      --show                      dump state.json
      --next                      show next pending dispatch decision
      mark-task <track> <phase> <task_id>
      render-tasks-md             write derived tasks.md to stdout
    """
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        sys.exit(2)

    change = sys.argv[1]
    global _RESOLVED_CHANGE
    _RESOLVED_CHANGE = change

    if len(sys.argv) < 3:
        # Default: --show
        subcommand = "--show"
    else:
        subcommand = sys.argv[2]

    try:
        project_root = _find_cwd_project_root()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    ps = PipelineState(change=change, project_root=project_root)

    if subcommand == "--show":
        print(json.dumps(ps.data, ensure_ascii=False, indent=2))
        return 0

    if subcommand == "--next":
        nd = ps.next_pending()
        if nd is None:
            print(json.dumps({"kind": None}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({
                "kind": nd.kind,
                "track": nd.track,
                "phase": nd.phase,
                "cycle": nd.cycle,
                "agent": nd.agent,
                "is_resume": nd.is_resume,
            }, ensure_ascii=False, indent=2))
        return 0

    if subcommand == "mark-task":
        # sys.argv: <script> <change> mark-task <track> <phase> <task_id>
        sys.exit(_cmd_mark_task(sys.argv[3:]))

    if subcommand == "render-tasks-md":
        print(ps.render_tasks_checkboxes(), end="")
        return 0

    print(f"Unknown subcommand: {subcommand}\n\n{USAGE}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    sys.exit(_main())