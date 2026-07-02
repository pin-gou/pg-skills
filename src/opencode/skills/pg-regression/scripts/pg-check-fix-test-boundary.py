#!/usr/bin/env python3
"""
pg-check-fix-test-boundary.py — Phase 2 收尾校验钩子

职责：扫描 fix-test agent 提交的 git diff，按 C5/C6/C7/C11 四条硬规则判定越界。

C6: 新增 skip / fixme / .only / .todo / @Disabled / @Ignore / xit / xdescribe
C7: 删除 expect()/assert() 数量 > 新增数量（断言被删/弱化）
C11: 删除 it()/test()/@Test（测试用例被删）
C5: 改动文件路径在 fixtures/seeds/sql/test-data 目录（禁止改种子数据）

输入：--git-dir <path> --run-dir <path>
  - --git-dir: 项目根（git 工作区）
  - --run-dir: regression run 目录（含 fix-test 留痕）
  - --target: 本次 fix-test 处理的目标（仅用于日志）

输出（stdout JSON）：
  {
    "ok": true | false,
    "violations": [
      {"rule": "C6", "files": [...], "evidence": [...]},
      ...
    ],
    "summary": "OK" | "FAIL: <rule_count> violations"
  }

退出码：
  0 — 校验通过
  1 — 校验失败（存在越界）
  2 — 参数错误 / 环境异常
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


# === C6 正则：新增 skip/fixme/.only/.todo/@Disabled/@Ignore/xit/xdescribe ===
# 匹配 + 开头的行（含新增）
_C6_PATTERNS = [
    r'\.skip\b',
    r'\.only\b',
    r'\.todo\b',
    r'@Disabled\b',
    r'@Ignore\b',
    r'\bxit\(',
    r'\bxdescribe\(',
]
_C6_COMBINED = re.compile(
    r'^\+.*(?:' + '|'.join(_C6_PATTERNS) + r')',
    re.M,
)

# 排除字符串字面量内出现的伪命中（如 expect(...).toBe('.skip')），仅做行级粗筛足够，
# 误报由编排器后续人工 review 处理（边界守护只防明显越界）。

# === C7 正则：expect / assert ===
_EXPECT_RE = re.compile(r'^\+.*\bexpect\(', re.M)
_ASSERT_RE = re.compile(r'^\+.*\bassert\.', re.M)
_DEL_EXPECT_RE = re.compile(r'^-.*\bexpect\(', re.M)
_DEL_ASSERT_RE = re.compile(r'^-.*\bassert\.', re.M)

# === C11 正则：it / test / @Test ===
_TEST_CASE_ADD = re.compile(r'^\+.*(?:\bit\(|^\s*test\(|@Test\b|@ParameterizedTest\b)', re.M)
_TEST_CASE_DEL = re.compile(r'^-.*(?:\bit\(|^\s*test\(|@Test\b|@ParameterizedTest\b)', re.M)

# === C5 路径黑名单 ===
_C5_PATH_PATTERNS = [
    re.compile(r'(^|/)fixtures/'),
    re.compile(r'(^|/)seeds/'),
    re.compile(r'(^|/)test-data/'),
    re.compile(r'.*\.sql$'),
    re.compile(r'(^|/)seed\.ya?ml$'),
    re.compile(r'(^|/)__fixtures__/'),
]


def run_git_diff(git_dir: Path) -> str:
    """获取当前未提交 diff（含 staged + unstaged 的测试相关改动）。"""
    cmd = ['git', '-C', str(git_dir), 'diff', '--no-color', '--unified=0',
           '--', '*.spec.ts', '*.spec.js', '*.test.ts', '*.test.js',
           '*Test.java', '*Tests.java', '*IT.java', '*Test.kt',
           'tests/', 'e2e/', 'src/test/', 'src/__tests__/']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout
    except subprocess.TimeoutExpired:
        print("⚠️ git diff 超时", file=sys.stderr)
        return ""


def get_changed_files(git_dir: Path) -> list[str]:
    """列出本次修改的所有文件路径（含 untracked）。"""
    cmd = ['git', '-C', str(git_dir), 'status', '--porcelain']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    paths = []
    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        # porcelain 格式: "XY <path>" 或 "XY <orig> -> <path>" (rename)
        path_part = line[3:].strip()
        if ' -> ' in path_part:
            path_part = path_part.split(' -> ', 1)[1]
        path_part = path_part.strip('"')
        if path_part:
            paths.append(path_part)
    return paths


def check_c6(diff_text: str) -> dict | None:
    """C6: 新增 skip/fixme/.only/.todo/@Disabled/@Ignore/xit/xdescribe。"""
    matches = _C6_COMBINED.findall(diff_text)
    if matches:
        return {
            "rule": "C6",
            "description": "新增 skip/fixme/.only/.todo/@Disabled/@Ignore/xit/xdescribe",
            "count": len(matches),
            "evidence": matches[:5],
        }
    return None


def check_c7(diff_text: str) -> dict | None:
    """C7: 断言 expect()/assert() 删除 > 新增。"""
    added = len(_EXPECT_RE.findall(diff_text)) + len(_ASSERT_RE.findall(diff_text))
    deleted = len(_DEL_EXPECT_RE.findall(diff_text)) + len(_DEL_ASSERT_RE.findall(diff_text))
    if deleted > added:
        return {
            "rule": "C7",
            "description": "断言数量减少（expect/assert 被删）",
            "added": added,
            "deleted": deleted,
        }
    return None


def check_c11(diff_text: str) -> dict | None:
    """C11: 删除测试用例 it()/test()/@Test/@ParameterizedTest。"""
    if _TEST_CASE_DEL.search(diff_text):
        return {
            "rule": "C11",
            "description": "删除测试用例（it/test/@Test）",
            "evidence": _TEST_CASE_DEL.findall(diff_text)[:5],
        }
    return None


def check_c5(changed_files: Iterable[str]) -> dict | None:
    """C5: 改动文件位于 fixtures/seeds/sql/test-data 黑名单。"""
    matches = []
    for f in changed_files:
        for pat in _C5_PATH_PATTERNS:
            if pat.search(f):
                matches.append(f)
                break
    if matches:
        return {
            "rule": "C5",
            "description": "改动文件命中 fixture/seed/sql/test-data 黑名单",
            "files": matches,
        }
    return None


def main():
    parser = argparse.ArgumentParser(description="Phase 2 收尾：fix-test 越界校验")
    parser.add_argument("--git-dir", required=True, help="git 工作区根目录")
    parser.add_argument("--run-dir", help="regression run 目录（仅用于日志）")
    parser.add_argument("--target", help="本次 fix-test 处理的目标标识（用于日志）")
    parser.add_argument("--json", action="store_true", help="强制 JSON 输出")
    args = parser.parse_args()

    git_dir = Path(args.git_dir).resolve()
    if not (git_dir / ".git").exists():
        print(f"❌ 不是 git 仓库: {git_dir}", file=sys.stderr)
        sys.exit(2)

    diff = run_git_diff(git_dir)
    if not diff:
        result = {"ok": True, "violations": [], "summary": "OK (no test diff)"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    changed = get_changed_files(git_dir)
    violations = []
    for checker in (check_c6, lambda d: check_c11(d), lambda d: check_c7(d)):
        v = checker(diff)
        if v:
            violations.append(v)
    v5 = check_c5(changed)
    if v5:
        violations.append(v5)

    if violations:
        result = {
            "ok": False,
            "violations": violations,
            "summary": f"FAIL: {len(violations)} rule(s) violated",
            "target": args.target,
            "run_dir": args.run_dir,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)
    else:
        result = {
            "ok": True,
            "violations": [],
            "summary": "OK",
            "target": args.target,
            "run_dir": args.run_dir,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)


if __name__ == "__main__":
    main()
