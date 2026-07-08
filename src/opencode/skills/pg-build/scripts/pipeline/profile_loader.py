"""v2.6: Code review profile loader.

职责：
1. 加载 .pg/code-review/code-review.yaml 的 profile 索引
2. 加载单个 profile（YAML 结构）
3. 加载 .pg/code-review/<profile>/<check>.md 规则文档
4. 解析 track 配置的优先级：用户显式 > language 自动派发 > default
5. Union 合并多个 profile：检查项并集 + weight=max + threshold=min

数据模型：
- Profile(name, language, checks: dict[str, CheckConfig], pass_threshold, escalate_threshold)
- CheckConfig(enabled, weight, doc)  # doc 指向 markdown 路径
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any


# 默认 profile 名（兜底）
DEFAULT_PROFILE_NAME = "default"

# Language → profile 映射（按 module_details[].language 自动派发）
LANGUAGE_PROFILE_MAP: dict[str, str] = {
    "java": "java-spring",
    "kotlin": "java-spring",
    "scala": "java-spring",
    "typescript": "vue3",
    "javascript": "vue3",
    "vue": "vue3",
    "go": "go",
    "golang": "go",
    "python": "default",
    "rust": "default",
}


@dataclasses.dataclass(frozen=True)
class CheckConfig:
    """单个检查项配置。"""

    enabled: bool
    weight: int
    doc: str = ""  # markdown 文件名（不含路径），位于 <profile_dir>/<doc>.md


@dataclasses.dataclass(frozen=True)
class Profile:
    """Profile 定义。"""

    name: str
    language: str = ""  # language 字段（用于 language 自动派发）
    inherit: str = ""   # 父 profile 名（chain merge）
    checks: tuple[tuple[str, CheckConfig], ...] = ()  # 有序 dict
    pass_threshold: int = 80
    escalate_threshold: int = 60

    def get_check(self, name: str) -> CheckConfig | None:
        for n, c in self.checks:
            if n == name:
                return c
        return None

    def check_names(self) -> list[str]:
        return [n for n, _ in self.checks]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "language": self.language,
            "inherit": self.inherit,
            "checks": {n: dataclasses.asdict(c) for n, c in self.checks},
            "pass_threshold": self.pass_threshold,
            "escalate_threshold": self.escalate_threshold,
        }


# ============================================================
# 路径解析
# ============================================================

def profile_dir(project_root: str) -> Path:
    """profile 规则目录：.pg/code-review/"""
    return Path(project_root) / ".pg" / "code-review"


def profile_index_path(project_root: str) -> Path:
    """profile 索引文件：.pg/code-review/code-review.yaml"""
    return Path(project_root) / ".pg" / "code-review" / "code-review.yaml"


# ============================================================
# YAML 加载
# ============================================================

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml as _yaml
    except ImportError:
        return {}
    with open(path, encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def _parse_check(name: str, raw: dict[str, Any]) -> CheckConfig:
    """解析单个检查项配置。"""
    if not isinstance(raw, dict):
        raw = {}
    return CheckConfig(
        enabled=bool(raw.get("enabled", True)),
        weight=int(raw.get("weight", 0)),
        doc=str(raw.get("doc", name)),
    )


def _parse_profile(name: str, raw: dict[str, Any]) -> Profile:
    """解析单个 profile。"""
    if not isinstance(raw, dict):
        raw = {}
    checks_raw = raw.get("checks") or {}
    checks = tuple(
        (n, _parse_check(n, c)) for n, c in checks_raw.items()
    )
    return Profile(
        name=name,
        language=str(raw.get("language", "")),
        inherit=str(raw.get("inherit", "")),
        checks=checks,
        pass_threshold=int(raw.get("pass_threshold", 80)),
        escalate_threshold=int(raw.get("escalate_threshold", 60)),
    )


def list_available_profiles(project_root: str) -> list[str]:
    """列出 .pg/code-review/code-review.yaml 中所有 profile 名。"""
    data = _load_yaml(profile_index_path(project_root))
    profiles = data.get("profiles") or {}
    return sorted(profiles.keys())


def load_profile(project_root: str, name: str) -> Profile:
    """从 .pg/code-review/code-review.yaml 加载单个 profile。

    找不到时返回 default profile（空骨架），避免硬失败。
    """
    data = _load_yaml(profile_index_path(project_root))
    profiles = data.get("profiles") or {}
    if name not in profiles:
        # fallback：返回空 profile（保留 name 用于错误提示）
        return Profile(name=name)
    return _parse_profile(name, profiles[name])


# ============================================================
# Markdown 规则读取
# ============================================================

def load_markdown_rule(project_root: str, profile_name: str, check_name: str) -> str:
    """加载 .pg/code-review/<profile_name>/<check_name>.md 规则文档。

    找不到时返回空字符串（review agent 用 prompt 中 inline fallback 兜底）。
    """
    base = profile_dir(project_root) / profile_name
    # 候选路径：<check_name>.md 或 profile_loader 写入的 doc 字段
    candidates = [base / f"{check_name}.md"]
    # 也尝试 profile_loader 注入的 doc 字段
    profile = load_profile(project_root, profile_name)
    check = profile.get_check(check_name)
    if check and check.doc and check.doc != check_name:
        candidates.insert(0, base / f"{check.doc}.md")
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


# ============================================================
# 优先级解析 + Union 合并
# ============================================================

def resolve_profile_names(
    track_code_review_profiles: tuple[str, ...] = (),  # v2.6 legacy 字段
    track_code_review_profile: str = "",  # v2.6 legacy 字段
    track_code_review_languages: tuple[str, ...] = (),
) -> list[str]:
    """v3.x: 按优先级解析要加载的 profile 链。

    v2.6 → v3.x 变化：
      - pg-build 内部 TrackState 删除 code_review_* 字段
      - 函数签名保留 v2.6 行为（兼容旧调用）
      - pg-build 主流程不传 track_code_review_profiles/profile（依赖 language 自动派发）
      - 显式 profile 优先于 language（保留 v2.6 行为）

    优先级（高 → 低）：
      1. track_code_review_profiles（用户显式，按顺序 = 优先级）
      2. track_code_review_profile（旧字段，单 profile）
      3. language 自动派发（按 module_details[].language）
      4. 默认 ['default']（兜底）

    返回有序列表（高优先级在前），Union 合并时按列表顺序应用。
    """
    # 1. 显式 profiles 列表（最高优先级，保留 v2.6 行为）
    if track_code_review_profiles:
        return list(track_code_review_profiles)

    # 2. legacy 单 profile（保留 v2.6 行为）
    if track_code_review_profile:
        return [track_code_review_profile]

    # 3. language 自动派发
    if track_code_review_languages:
        lang_profiles: list[str] = []
        seen: set[str] = set()
        for lang in track_code_review_languages:
            mapped = LANGUAGE_PROFILE_MAP.get(lang, DEFAULT_PROFILE_NAME)
            if mapped not in seen:
                seen.add(mapped)
                lang_profiles.append(mapped)
        if lang_profiles:
            return lang_profiles

    # 4. 兜底 default
    return [DEFAULT_PROFILE_NAME]


def load_effective_profile(
    project_root: str,
    profile_names: list[str],
) -> Profile:
    """v2.6: Union 合并多个 profile 为 effective profile。

    Union 语义：
      - checks: 并集（包含 inherit 链），weight 取 max，enabled 取 OR
      - pass_threshold: 只取用户显式 profile_names 的 min（不继承自 default）
      - escalate_threshold: 同上
      - inherit 链：展开以收集所有 check 项

    设计理由：threshold 是"严格度"指标，default profile 的 80 不应稀释
    用户显式指定的 security 90。
    """
    if not profile_names:
        profile_names = [DEFAULT_PROFILE_NAME]

    # Step 1: 加载"显示指定"的 profile（用于 threshold）
    explicit_loaded: list[Profile] = []
    seen: set[str] = set()
    for name in profile_names:
        if name in seen:
            continue
        seen.add(name)
        explicit_loaded.append(load_profile(project_root, name))

    # Step 2: 展开 inherit 链，收集所有 check 项
    full_loaded: list[Profile] = list(explicit_loaded)
    visited: set[str] = {p.name for p in explicit_loaded}
    queue: list[str] = [
        p.inherit for p in explicit_loaded if p.inherit and p.inherit not in visited
    ]
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        prof = load_profile(project_root, name)
        full_loaded.append(prof)
        if prof.inherit and prof.inherit not in visited:
            queue.insert(0, prof.inherit)

    # Step 3: Union 合并 checks（用 full_loaded 含 inherit）
    merged_checks: dict[str, CheckConfig] = {}
    for prof in full_loaded:
        for name, check in prof.checks:
            if name in merged_checks:
                old = merged_checks[name]
                merged_checks[name] = CheckConfig(
                    enabled=old.enabled or check.enabled,
                    weight=max(old.weight, check.weight),
                    doc=old.doc or check.doc,
                )
            else:
                merged_checks[name] = check

    # Step 4: threshold 只用 explicit_loaded 的 min（不含 default inherit）
    pass_th = 100
    esc_th = 100
    for prof in explicit_loaded:
        pass_th = min(pass_th, prof.pass_threshold)
        esc_th = min(esc_th, prof.escalate_threshold)

    # fallback
    if pass_th == 100:
        pass_th = 80
    if esc_th == 100:
        esc_th = 60

    effective_name = profile_names[0]

    return Profile(
        name=effective_name,
        language="",
        inherit="",
        checks=tuple(merged_checks.items()),
        pass_threshold=pass_th,
        escalate_threshold=esc_th,
    )


def resolve_profile_for_track(
    project_root: str,
    track_code_review_profiles: tuple[str, ...] = (),  # v2.6 legacy
    track_code_review_profile: str = "",  # v2.6 legacy
    track_code_review_languages: tuple[str, ...] = (),
) -> Profile:
    """v3.x: 单步解析 — 给定 track 的 module_languages，返回 effective profile。

    v2.6 → v3.x 变化：
      - pg-build 不再传 track_code_review_profiles / track_code_review_profile
      - 这些参数保留以兼容旧调用（被忽略）
      - profile 完全由 language 自动派发（参考 resolve_profile_names）

    这是 orchestrator 应该调用的主入口。
    """
    names = resolve_profile_names(
        track_code_review_profiles,
        track_code_review_profile,
        track_code_review_languages,
    )
    return load_effective_profile(project_root, names)


# ============================================================
# Score 计算（review agent 用）
# ============================================================

def compute_review_score(
    profile: Profile,
    check_results: dict[str, bool],  # {check_name: pass?}
) -> int:
    """根据 enabled 检查项 + 权重 + 通过情况计算 review_score。

    公式：score = sum(weight for enabled & pass) / sum(weight for enabled) * 100
    返回 0-100 的整数。
    """
    if not profile.checks:
        return 100

    total_weight = 0
    passed_weight = 0
    for name, check in profile.checks:
        if not check.enabled:
            continue
        total_weight += check.weight
        if check_results.get(name, False):
            passed_weight += check.weight

    if total_weight == 0:
        return 100

    return int(round(passed_weight * 100.0 / total_weight))


def decide_review_disposition(
    profile: Profile, review_score: int,
) -> str:
    """根据 score 与 threshold 决定 disposition。

    返回值：
      - "completed": score ≥ pass_threshold → 进入 verify
      - "escalate": pass_threshold > score ≥ escalate_threshold → fix-review
      - "failed": score < escalate_threshold → workflow_failed
    """
    if review_score >= profile.pass_threshold:
        return "completed"
    if review_score >= profile.escalate_threshold:
        return "escalate"
    return "failed"