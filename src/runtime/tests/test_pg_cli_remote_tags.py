"""Tests for pg CLI remote version listing (must not import upstream tags)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
PG_CLI = REPO_ROOT / "src" / "runtime" / "bin" / "pg"
PG_RUN = REPO_ROOT / "src" / "runtime" / "bin" / "pg-run"
RUNTIME_LIB = REPO_ROOT / "src" / "runtime" / "lib"


def _load_script(module_name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_pg_cli():
    return _load_script("pg_cli_under_test", PG_CLI)


def _load_pg_run():
    added = str(RUNTIME_LIB) not in sys.path
    if added:
        sys.path.insert(0, str(RUNTIME_LIB))
    try:
        return _load_script("pg_run_under_test", PG_RUN)
    finally:
        if added:
            sys.path.remove(str(RUNTIME_LIB))


class TestRemoteVersionTags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = _load_pg_cli()

    def test_parses_annotated_and_lightweight_tags(self):
        output = (
            "aaaa\trefs/tags/v1.0.0\n"
            "bbbb\trefs/tags/v1.0.0^{}\n"
            "cccc\trefs/tags/v1.0.1\n"
            "dddd\trefs/tags/v1.0.10\n"
            "eeee\trefs/tags/v1.0.2\n"
        )
        self.assertEqual(
            self.pg._parse_remote_version_tags(output),
            ["v1.0.0", "v1.0.1", "v1.0.2", "v1.0.10"],
        )

    def test_ignores_non_version_refs_and_garbage(self):
        output = (
            "aaaa\trefs/tags/nightly\n"
            "bbbb\trefs/heads/main\n"
            "cccc\trefs/tags/v0.9.4\n"
            "not-a-refspec-line\n"
            "\n"
        )
        self.assertEqual(self.pg._parse_remote_version_tags(output), ["v0.9.4"])

    def test_empty_output(self):
        self.assertEqual(self.pg._parse_remote_version_tags(""), [])


class TestUpgradeListDoesNotCreateTags(unittest.TestCase):
    """`pg upgrade --list` must use ls-remote and never `git fetch --tags`."""

    @classmethod
    def setUpClass(cls):
        cls.pg = _load_pg_cli()

    def test_list_uses_ls_remote(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            result = mock.Mock()
            result.returncode = 0
            if 'get-url' in cmd:
                result.stdout = 'git@github.com:pin-gou/pg-skills.git\n'
            elif 'ls-remote' in cmd:
                result.stdout = "deadbeef\trefs/tags/v1.0.1\n"
            else:
                result.stdout = ''
            result.stderr = ''
            return result

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(self.pg.subprocess, 'run', side_effect=fake_run):
                rc = self.pg._upgrade_check(Path(tmp), '0.9.4')

        self.assertEqual(rc, 0)
        joined = [' '.join(c) for c in calls]
        self.assertTrue(any('ls-remote' in c for c in joined), joined)
        # 列表模式不应触发任何 fetch（fetch --tags 会把上游 tag 落进项目）
        self.assertFalse(any('fetch' in c.split() for c in joined), joined)
        # tag 自动跟随必须被关闭，否则后续 fetch 会把上游 tag 带进项目
        self.assertTrue(
            any('remote.pg-skills.tagOpt' in c and '--no-tags' in c for c in joined),
            joined,
        )


class TestPgRunUpdateTargets(unittest.TestCase):
    """pg-run 更新 Tab 的置顶项必须指向远端默认分支 v1（main 已删除）。"""

    @classmethod
    def setUpClass(cls):
        cls.pg_run = _load_pg_run()

    def test_default_ref_is_v1(self):
        self.assertEqual(self.pg_run._UPDATE_DEFAULT_REF, 'v1')

    def test_default_ref_is_pinned_first(self):
        targets = self.pg_run._build_update_targets(['v1.0.0', 'v1.0.1', 'v0.9.4'])
        self.assertEqual(targets[0], 'v1')
        self.assertEqual(targets[1:], ['v1.0.1', 'v1.0.0', 'v0.9.4'])

    def test_default_ref_not_duplicated_from_tags(self):
        targets = self.pg_run._build_update_targets(['v1', 'v1.0.0'])
        self.assertEqual(targets, ['v1', 'v1.0.0'])


if __name__ == '__main__':
    unittest.main()
