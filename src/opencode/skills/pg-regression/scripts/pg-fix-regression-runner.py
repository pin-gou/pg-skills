#!/usr/bin/env python3
"""
pg-fix-regression-runner.py — OS 层串行循环，修复生产代码 regression issue。

职责：
1. 读取 .pg/regression/*.json (pg-regression 产出)
2. 对每条 issue 创建独立分支 → 调用 fix-prod agent → git push → 创建 PR → 切回 master
3. 写入修复结果 + 汇总报告

关键设计：
- 循环在 OS 进程层 (subprocess.run 阻塞)，物理上无法并发
- Git 操作 (branch/push) 由 runner 直接执行，LLM 不参与调度
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
SKILLS_DIR = RUNNER_DIR.parent.parent
PROJECT_ROOT = Path.cwd()

ISSUES_DIR = PROJECT_ROOT / ".pg" / "regression"
DEFAULT_BRANCH = "master"

GITEE_TOKEN = os.environ.get("GITEE_TOKEN", "")
GITEE_API_BASE = os.environ.get("GITEE_API_BASE", "https://gitee.com/api/v5")

FIX_PROD_AGENT = "pg-regression/fix-prod"

# ==================== Run dir resolution ====================

def resolve_run_dir(cli_arg: str | None, project_root: Path) -> Path:
    if cli_arg:
        p = Path(cli_arg)
        if not p.is_absolute():
            p = project_root / p
        return p.resolve()

    reg_dir = project_root / ".pg" / "regression"
    candidates = sorted(
        [p for p in reg_dir.iterdir()
         if p.is_dir() and re.match(r"^[a-z][a-z0-9-]*-\d{8}-\d{2}$", p.name)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0].resolve()

    sys.stderr.write(
        "Warning: no --run-dir given and no <suite>-<date>-<NN> dir found; "
        "using .pg/regression/\n"
    )
    return reg_dir.resolve()


# ==================== Helpers ====================

def _make_slug(title: str) -> str:
    slug = ''.join(c if c.isalnum() else '-' for c in title.lower())[:30]
    slug = '-'.join(x for x in slug.split('-') if x)[:30]
    return slug or "fix"


def _make_branch_name(suite: str, title: str, issue_id: str = "") -> str:
    slug = _make_slug(title)
    # Use issue_id as hash source if available
    h = issue_id[-6:] if issue_id and len(issue_id) >= 6 else hashlib.md5(f"{suite}:{title}".encode()).hexdigest()[:6]
    return f"fix/{suite}-{slug}-{h}"


def _run_git(*args, timeout=30):
    """Run a git command, raise on failure."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"⚠️ git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        result.check_returncode()
    return result.stdout.strip()


def _detect_owner_repo():
    """Parse owner/repo from git remote origin."""
    url = _run_git("remote", "get-url", "origin")
    # Normalize: strip scheme/host prefix and .git suffix
    cleaned = url
    for prefix in [
        "git@gitee.com:", "https://gitee.com/", "http://gitee.com/",
        "git@github.com:", "https://github.com/", "http://github.com/",
        "git@", "https://", "http://",
    ]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    cleaned = cleaned.removesuffix(".git")
    return cleaned


def _create_gitee_pr(branch, base, title, body):
    """Create a PR via Gitee Open API. Returns (pr_url, pr_number)."""
    if not GITEE_TOKEN:
        print("❌ GITEE_TOKEN 未设置，跳过 PR 创建", file=sys.stderr)
        return None, None

    owner_repo = _detect_owner_repo()
    api_url = f"{GITEE_API_BASE.rstrip('/')}/repos/{owner_repo}/pulls"

    payload = {
        "head": branch,
        "base": base,
        "title": title,
        "body": body,
        "auto_merge": False,
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"token {GITEE_TOKEN}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        if 400 <= e.code < 500 and ("已存在" in body or "already exists" in body.lower()):
            print("  ⚠️  PR 已存在，跳过创建", file=sys.stderr)
            return None, None
        print(f"  ❌ 创建 PR 失败 ({e.code}): {body}", file=sys.stderr)
        return None, None
    except Exception as e:
        print(f"  ❌ 创建 PR 异常: {e}", file=sys.stderr)
        return None, None

    pr_url = data.get("html_url", "")
    pr_number = data.get("number")
    return pr_url, pr_number


# ==================== Per-issue processing ====================

def process_issue(issue: dict, suite: str, run_dir: Path) -> dict:
    """Process a single issue: branch → fix → push → PR → cleanup."""
    idx = issue.get("id", "unknown")
    title = issue.get("title", "")
    branch = issue.get("branchName", "")
    if not branch:
        branch = _make_branch_name(suite, title, idx)

    result = {
        "id": idx, "suite": suite, "title": title,
        "branchName": branch, "status": "pending",
        "commitSha": None, "filesChanged": [],
        "prUrl": None, "prNumber": None,
        "errorMessage": None, "duration_s": 0,
    }
    start = time.time()

    print(f"\n{'='*60}")
    print(f"处理 #{idx}: [{suite}] {title}")
    print(f"  分支: {branch}")
    print(f"{'='*60}")

    # 1. Git: checkout master → pull → create branch
    try:
        _run_git("checkout", DEFAULT_BRANCH)
        _run_git("pull", "--ff-only", "origin", DEFAULT_BRANCH)
    except subprocess.CalledProcessError as e:
        result["status"] = "failed"
        result["errorMessage"] = f"git checkout/pull failed: {e.stderr.strip()}"
        print(f"  ❌ {result['errorMessage']}", file=sys.stderr)
        return result
    # Delete existing branch if present (non-fatal)
    try:
        _run_git("branch", "-D", branch, timeout=10)
    except subprocess.CalledProcessError:
        pass
    try:
        _run_git("checkout", "-b", branch)
    except subprocess.CalledProcessError as e:
        result["status"] = "failed"
        result["errorMessage"] = f"git branch failed: {e.stderr.strip()}"
        print(f"  ❌ {result['errorMessage']}", file=sys.stderr)
        return result

    # 2. Write prompt file (Markdown format)
    branch_slug = branch.replace("fix/", "", 1)
    issue_dir = run_dir / "fix-issues" / f"{idx}-{branch_slug}"
    issue_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = issue_dir / "1-prompt.md"
    test_targets_str = ', '.join(issue.get('test_targets', [])) or '(无)'
    prompt_text = f"""# Issue #{idx}: {title}

## 上下文

| 字段 | 值 |
|------|-----|
| Suite | {suite} |
| Component | {issue.get('component', '')} |
| 源文件 | {issue.get('file', '')} |
| 默认分支 | {DEFAULT_BRANCH} |
| 修复分支 | `{branch}` |
| 受影响的测试 | {test_targets_str} |

## 预期行为

{issue.get('expected', '')}

## 实际行为

{issue.get('actual', '')}

## 根因

{issue.get('description', '')}

## 指令

请按 fix-prod 工作流执行：检查工作区 → 加载 pg-fix-issue SKILL 修复 → git commit → 输出 JSON。
"""
    prompt_file.write_text(prompt_text, encoding="utf-8")

    # 3. Call opencode run (LLM entry, blocked)
    print(f"  🚀 启动 opencode run --agent {FIX_PROD_AGENT} ...")
    try:
        fix_proc = subprocess.run(
            ["opencode", "run",
             "--agent", FIX_PROD_AGENT,
             "--file", str(prompt_file),
             f"修复 #{idx}: {title}"],
            capture_output=True, text=True,
            timeout=7200,  # 2h per issue max
        )
        # Persist agent output for audit
        agent_log = issue_dir / "2-agent.log"
        agent_log.write_text(
            f"=== STDOUT ===\n{fix_proc.stdout}\n=== STDERR ===\n{fix_proc.stderr}\n=== EXIT: {fix_proc.returncode} ===",
            encoding="utf-8")
    except subprocess.TimeoutExpired:
        result["status"] = "failed"
        result["errorMessage"] = "subprocess timeout (2h)"
        print(f"  ❌ 超时", file=sys.stderr)
        # Persist partial agent output if available
        agent_log = issue_dir / "2-agent.log"
        agent_log.write_text("=== TIMEOUT (2h) ===\nstdout/stderr not available", encoding="utf-8")
        _cleanup(branch)
        return result

    if fix_proc.returncode != 0:
        result["status"] = "failed"
        result["errorMessage"] = f"fix-prod exit code {fix_proc.returncode}"
        # Try to parse JSON from stdout even on failure
        proc_out = (fix_proc.stdout or "") + (fix_proc.stderr or "")
        if ">>>EARLY_EXIT" in proc_out:
            result["errorMessage"] = "fix-prod early exit"
        elif "escalate" in proc_out.lower():
            result["status"] = "escalate"
        print(f"  ❌ fix-prod 返回非零: {fix_proc.returncode}", file=sys.stderr)
        _cleanup(branch)
        return result

    # Parse result JSON from stdout
    result_json = _parse_result_json(fix_proc.stdout)
    if result_json:
        result["status"] = result_json.get("status", "failed")
        result["commitSha"] = result_json.get("commitSha")
        result["filesChanged"] = result_json.get("filesChanged", [])
        result["errorMessage"] = result_json.get("errorMessage")
    else:
        result["status"] = "failed"
        result["errorMessage"] = "no parseable JSON in fix-prod output"
        _cleanup(branch)
        return result

    if result["status"] != "success":
        _cleanup(branch)
        return result

    # 4. Git push (OS layer, no LLM)
    try:
        _run_git("push", "-u", "origin", branch, timeout=120)
    except subprocess.CalledProcessError as e:
        result["status"] = "failed"
        result["errorMessage"] = f"git push failed: {e.stderr.strip()}"
        print(f"  ❌ {result['errorMessage']}", file=sys.stderr)
        _cleanup(branch)
        return result

    # 5. Create PR (OS layer, no LLM)
    pr_body = (
        f"## 修复: {title}\n\n"
        f"由 pg-fix-regression-runner 自动修复并提交 PR。\n\n"
        f"### 问题描述\n{issue.get('description', title)}\n"
        f"### 期望\n{issue.get('expected', '')}\n"
        f"### 实际\n{issue.get('actual', '')}\n\n"
        f"---\n**禁止自动 merge** - 需人工审核后合并。"
    )
    pr_title = f"fix({suite}): {title}"
    pr_url, pr_number = _create_gitee_pr(branch, DEFAULT_BRANCH, pr_title, pr_body)
    if pr_url:
        result["prUrl"] = pr_url
        result["prNumber"] = pr_number
        print(f"  ✅ PR 已创建: {pr_url}")
    else:
        result["status"] = "failed"
        result["errorMessage"] = "PR creation failed"
        _cleanup(branch)
        return result

    # 6. Write result file
    result["duration_s"] = round(time.time() - start, 1)
    _write_result_file(result, run_dir)

    # 7. Remove fixed issue from suite JSON file
    _remove_issue_from_suite_file(suite, idx)

    # 8. Cleanup
    _cleanup(branch)

    return result


def _parse_result_json(stdout: str) -> dict | None:
    """Try to parse JSON from fix-prod agent's stdout."""
    # Look for JSON block (between ```json and ``` if present)
    m = re.search(r'```json\s*\n(.*?)\n```', stdout, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find a JSON object in the output (last JSON-like block)
    for line in reversed(stdout.strip().split('\n')):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    return None


def _cleanup(branch):
    """Checkout master and delete branch."""
    try:
        _run_git("checkout", DEFAULT_BRANCH, timeout=10)
        _run_git("branch", "-D", branch, timeout=10)
    except subprocess.CalledProcessError:
        pass


def _write_result_file(result: dict, run_dir: Path):
    """Write per-issue result JSON to fix-issues/<idx>-<slug>/3-result.json."""
    idx = result.get("id", "unknown")
    suite = result.get("suite", "unknown")
    branch = result.get("branchName", f"fix/{suite}-unknown")
    branch_slug = branch.replace("fix/", "", 1)
    issue_dir = run_dir / "fix-issues" / f"{idx}-{branch_slug}"
    issue_dir.mkdir(parents=True, exist_ok=True)
    result_file = issue_dir / "3-result.json"
    result_file.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  📝 结果写入: {result_file}")


def _remove_issue_from_suite_file(suite: str, issue_id: str):
    """Remove a fixed issue from .pg/regression/<suite>.json."""
    suite_file = ISSUES_DIR / f"{suite}.json"
    if not suite_file.exists():
        return
    try:
        data = json.loads(suite_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    before = len(data.get("issues", []))
    data["issues"] = [i for i in data.get("issues", []) if i.get("id") != issue_id]
    after = len(data["issues"])
    if after < before:
        suite_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  🧹 已从 {suite}.json 移除 issue #{issue_id}")


# ==================== Summary ====================

def write_summary(results: list[dict], run_dir: Path):
    """Write final summary report."""
    run_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y%m%d-%H%M")
    summary_file = run_dir / "fix-issue-runner-summary.md"

    success = [r for r in results if r["status"] == "success"]
    escalate = [r for r in results if r["status"] == "escalate"]
    failed = [r for r in results if r["status"] == "failed"]
    total = len(results)

    lines = []
    lines.append(f"# pg-fix-regression-runner 汇总报告\n")
    lines.append(f"**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**总 issue**: {total}\n")
    lines.append(f"## 统计\n")
    lines.append(f"| 状态 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| ✅ 已创建 PR | {len(success)} |")
    lines.append(f"| ⚠️ Escalate | {len(escalate)} |")
    lines.append(f"| ❌ Failed | {len(failed)} |")
    lines.append("")

    if success:
        lines.append("## ✅ 成功创建 PR\n")
        lines.append("| # | Suite | Issue | PR |")
        lines.append("|---|-------|------|-----|")
        for r in success:
            pr_link = f"[PR #{r.get('prNumber', '')}]({r.get('prUrl', '#')})" if r.get("prUrl") else "N/A"
            lines.append(f"| {results.index(r)+1} | {r['suite']} | {r['title']} | {pr_link} |")
        lines.append("")

    if escalate:
        lines.append("## ⚠️ Escalate\n")
        lines.append("| # | Suite | Issue | 原因 |")
        lines.append("|---|-------|------|------|")
        for r in escalate:
            lines.append(f"| {results.index(r)+1} | {r['suite']} | {r['title']} | {r.get('errorMessage', 'pg-fix-issue 触发 ESCALATE')} |")
        lines.append("")

    if failed:
        lines.append("## ❌ Failed\n")
        lines.append("| # | Suite | Issue | 原因 |")
        lines.append("|---|-------|------|------|")
        for r in failed:
            lines.append(f"| {results.index(r)+1} | {r['suite']} | {r['title']} | {r.get('errorMessage', 'unknown')} |")
        lines.append("")

    lines.append("---\n")
    lines.append(f"**结果文件**: `{run_dir}/fix-issues/`")

    summary_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📋 汇总报告: {summary_file}")


# ==================== Main loop ====================

def main():
    parser = argparse.ArgumentParser(
        description="Fix production code regression issues from .pg/regression/<suite>.json"
    )
    parser.add_argument("--run-dir", default=None,
                        help="Run directory (e.g. .pg/regression/backend-20260627-01). "
                             "Sets fix-issues/ and summary output paths. "
                             "Defaults to the latest <suite>-<date>-<NN> dir.")
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run_dir, PROJECT_ROOT)

    print("=" * 60)
    print("pg-fix-regression-runner 启动")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Run dir: {run_dir}")
    print("=" * 60)

    # 1. Read all issue JSON files
    all_issues = []
    suite_files = sorted(p for p in ISSUES_DIR.glob("*.json") if not p.name.startswith("summary-"))
    if not suite_files:
        print("❌ .pg/regression/ 中无 suite JSON 文件")
        print("   请先运行 pg-regression 产出问题清单")
        sys.exit(0)

    for f in suite_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"⚠️  跳过 {f}: JSON 解析失败: {e}", file=sys.stderr)
            continue
        suite = data.get("suite", f.stem)
        for iss in data.get("issues", []):
            iss["_suite"] = suite
            if not iss.get("id"):
                slug = _make_slug(iss.get("title", "fix"))
                h = hashlib.md5(f"{suite}:{iss.get('title', '')}".encode()).hexdigest()[:6]
                iss["id"] = f"{slug}-{h}"
            all_issues.append(iss)

    if not all_issues:
        print("无生产代码问题待修复")
        sys.exit(0)

    print(f"\n共 {len(all_issues)} 个 production-code issue 待处理:\n")
    for iss in all_issues:
        print(f"  [{iss['_suite']}] {iss.get('id', '?')}: {iss.get('title', '')}")
    print()

    # 2. Serial loop over all issues
    results = []
    for iss in all_issues:
        suite = iss["_suite"]
        r = process_issue(iss, suite, run_dir)
        results.append(r)

    # 3. Summary
    write_summary(results, run_dir)

    success = sum(1 for r in results if r["status"] == "success")
    print(f"\n{'='*60}")
    print(f"完成: {success}/{len(results)} 成功")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()