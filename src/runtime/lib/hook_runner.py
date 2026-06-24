#!/usr/bin/env python3
"""pg-skills hook runner — invoke hook scripts, read result.json, apply error policy.

Phase 2 实现. 是 L1 runner 与项目侧 hook 的桥梁.

Usage:
    from hook_runner import HookRunner
    runner = HookRunner(pg_skills_path, project_root)
    result = runner.invoke('module', 'test', module='backend', test_key='unit')
    runner.handle_failure(result, retry_count=1, track_cfg={...})
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ----- Hook 解析: 4 级查找 -----

def resolve_hook_path(
    project_yaml: dict,
    hook_type: str,
    target: str,
    *,
    env: str | None = None,
    pg_skills_path: Path,
) -> Path:
    """4 级查找: override → 命名约定 → env-aware 命名约定 → 内置 default template.

    Args:
        project_yaml: 解析后的 .pg/project.yaml
        hook_type: 'build' | 'test' | 'lint' | 'start' | 'stop' | 'logs' | 'tail' | 'prepare' | 'clean' | 'health' ...
        target: module 名 / role 名 / env 名 / invariant 名 (取决于 hook_type)
        env: 当前激活的 environment (role / env hook 才需要)
        pg_skills_path: pg-skills 仓库根

    Returns:
        可执行的 hook 脚本路径
    """
    pg_dir = Path(project_yaml.get('pg_dir', '.pg'))
    hooks_dir = pg_dir / 'hooks'

    # 1. project.yaml 显式 override
    module_cfg = project_yaml.get('modules', {}).get(target, {})
    if hook_type in module_cfg.get('hooks', {}):
        return pg_dir / module_cfg['hooks'][hook_type]

    env_cfg = project_yaml.get('environments', {}).get(env or '', {})
    if hook_type in env_cfg.get('hooks', {}):
        return pg_dir / env_cfg['hooks'][hook_type]

    # 2. 命名约定 (按 hook_type 分类)
    candidates = []
    if hook_type in ('build', 'test', 'lint'):
        # module hook: {module}-{action}.sh 或 {action}-{module}.sh
        candidates.extend([
            hooks_dir / f'{target}-{hook_type}.sh',
            hooks_dir / f'{hook_type}-{target}.sh',
        ])
    elif hook_type in ('start', 'stop', 'logs', 'tail'):
        # role hook: role-{role}-{action}.sh
        candidates.extend([
            hooks_dir / f'role-{target}-{hook_type}.sh',
        ])
    elif hook_type in ('prepare', 'clean', 'health', 'verify'):
        # env hook: env-{env}-{action}.sh
        candidates.extend([
            hooks_dir / f'env-{target}-{hook_type}.sh',
            hooks_dir / f'env-{env}-{hook_type}.sh' if env else None,
        ])
    elif hook_type.startswith('invariant-'):
        # invariant hook: invariant-{name}.sh
        name = hook_type.removeprefix('invariant-')
        candidates.append(hooks_dir / f'invariant-{name}.sh')

    # 3. env-aware 命名约定
    if env:
        candidates_with_env = []
        for c in candidates:
            if c is None:
                continue
            candidates_with_env.extend([
                c.with_suffix(f'.{env}.sh'),
                c,
            ])
        candidates = candidates_with_env

    for c in candidates:
        if c is None:
            continue
        if c.exists() and os.access(c, os.X_OK):
            return c

    # 4. 内置 default template (按 language / env-type)
    language = module_cfg.get('language', project_yaml.get('language', ''))
    env_type = env_cfg.get('type', '')
    template = _find_template(pg_skills_path, hook_type, language=language, env_type=env_type)
    if template:
        return template

    raise FileNotFoundError(
        f'No hook found for type={hook_type} target={target} env={env}. '
        f'Looked in {hooks_dir}, then pg-skills templates.'
    )


def _find_template(
    pg_skills_path: Path,
    hook_type: str,
    *,
    language: str = '',
    env_type: str = '',
) -> Path | None:
    """在 examples/ 下找 default template."""
    examples_dir = pg_skills_path / 'examples'

    # language-based template (例如 examples/java-maven/hooks/module-test.sh)
    if language:
        candidates = [
            examples_dir / language / 'hooks' / f'module-{hook_type}.sh',
            examples_dir / language / 'hooks' / f'{hook_type}.sh',
        ]
        for c in candidates:
            if c.exists():
                return c

    # env-type-based template
    if env_type:
        candidate = examples_dir / f'env-{env_type}' / f'env-{hook_type}.sh'
        if candidate.exists():
            return candidate

    return None


# ----- Hook 调用 -----

@dataclass
class HookResult:
    status: str  # 'pass' | 'fail' | 'blocked' | 'timeout' | 'running'
    exit_code: int = 0
    duration_seconds: float = 0.0
    error: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> 'HookResult':
        data = json.loads(path.read_text(encoding='utf-8'))
        return cls(
            status=data.get('status', 'unknown'),
            exit_code=data.get('exit_code', 0),
            duration_seconds=data.get('duration_seconds', 0.0),
            error=data.get('error', {}),
            metadata=data.get('metadata', {}),
            artifacts=data.get('artifacts', {}),
            raw=data,
        )


class HookRunner:
    def __init__(self, pg_skills_path: Path, project_root: Path):
        self.pg_skills_path = pg_skills_path
        self.project_root = project_root

    def invoke(
        self,
        hook_type: str,
        target: str,
        *,
        env: str | None = None,
        extra_args: dict | None = None,
        timeout_seconds: int = 1800,
    ) -> HookResult:
        """调用 hook, 传 CLI flags + env 变量, 等退出, 读 result.json."""
        project_yaml = self._load_project_yaml()

        hook_path = resolve_hook_path(
            project_yaml, hook_type, target,
            env=env, pg_skills_path=self.pg_skills_path,
        )

        # 准备 result / log 文件
        run_id = _make_run_id()
        run_dir = self.project_root / '.pg' / 'runs' / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result_file = run_dir / f'{target}-{hook_type}.json'
        log_file = run_dir / f'{target}-{hook_type}.log'

        # 准备 env 变量
        module_cfg = project_yaml.get('modules', {}).get(target, {})
        module_root = self.project_root / module_cfg.get('root', '.')
        env_vars = {
            **os.environ,
            'PG_SKILLS_PATH': str(self.pg_skills_path),
            'PG_PROJECT_ROOT': str(self.project_root),
            'PG_MODULE_ROOT': str(module_root),
            'PG_RESULT_FILE': str(result_file),
            'PG_LOG_FILE': str(log_file),
            'PG_MODULE': target,
            'PG_HOOK_TYPE': hook_type,
            'PG_ENV': env or '',
        }

        # 准备 CLI flags
        cmd = [str(hook_path)]
        cmd.append(f'--{hook_type}')
        if env:
            cmd.append(f'--env={env}')
        if extra_args:
            for k, v in extra_args.items():
                cmd.append(f'--{k}={v}')

        # 执行
        proc = subprocess.run(
            cmd, env=env_vars, timeout=timeout_seconds, cwd=str(self.project_root),
        )

        # 读取 result.json
        if result_file.exists():
            return HookResult.from_file(result_file)

        # hook 没写 result.json, 用 exit code 兜底
        return HookResult(
            status='pass' if proc.returncode == 0 else 'fail',
            exit_code=proc.returncode,
            error={'category': 'unknown', 'message': f'Hook exited {proc.returncode} without writing result.json'},
        )

    def _load_project_yaml(self) -> dict:
        """最小化的 project.yaml loader (Phase 1 用; Phase 2 替换为 jsonschema 校验版)."""
        import yaml  # type: ignore
        yaml_path = self.project_root / '.pg' / 'project.yaml'
        if not yaml_path.exists():
            return {}
        return yaml.safe_load(yaml_path.read_text(encoding='utf-8'))


def _make_run_id() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y%m%d-%H%M%S')


# ----- 失败处理决策 -----

DECISION_ESCALATE = 'escalate'
DECISION_RETRY_AFTER = 'retry_after'
DECISION_RETRY_IMMEDIATELY = 'retry_immediately'


def handle_hook_failure(
    result: HookResult,
    retry_count: int,
    track_max_fail_retries: int,
    error_categories: dict,
) -> tuple[str, int]:
    """基于 error-categories.yaml + retry_count 决策下一步.

    Returns:
        (decision, wait_seconds) 元组
    """
    category = result.error.get('category', 'unknown')
    cat_cfg = error_categories.get(category, error_categories.get('unknown', {}))

    severity = result.error.get('severity', cat_cfg.get('default_severity', 'recoverable'))
    recoverable = result.metadata.get('agent_recoverable', cat_cfg.get('default_agent_recoverable', False))
    max_retries = result.metadata.get('max_retries', cat_cfg.get('default_max_retries', 0))

    # blocked / fatal: 立即 escalate
    if severity in ('blocked', 'fatal'):
        return DECISION_ESCALATE, 0

    # 超过最大重试次数: 升级 severity, escalate
    effective_max = min(max_retries, track_max_fail_retries)
    if not recoverable or retry_count >= effective_max:
        return DECISION_ESCALATE, 0

    # 计算退避时间
    retry_strategy = cat_cfg.get('default_retry_strategy', 'none')
    if retry_strategy == 'exponential_backoff':
        wait_seconds = [1, 3, 10][min(retry_count, 2)]
    elif retry_strategy == 'wait_and_retry':
        wait_seconds = result.metadata.get('retry_after_seconds', 5)
    elif retry_strategy == 'after_fix':
        wait_seconds = result.metadata.get('retry_after_seconds', 3)
    else:
        wait_seconds = 0
        return DECISION_ESCALATE, 0  # strategy=none

    return DECISION_RETRY_AFTER, wait_seconds
