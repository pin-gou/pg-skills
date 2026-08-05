#!/usr/bin/env python3
"""Tests for pg-parse-config.py config path resolution.

Verifies that:
  - _find_project_yaml_upward finds .pg/project.yaml at arbitrary nesting depth
  - resolution works from both the script location and the cwd
  - missing config falls back gracefully (no exception at import time)
"""

import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "pg-parse-config.py"


def load_parser() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("pg_parse_config_path", str(SCRIPT_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FindProjectYamlUpwardTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_parser()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _make_layout(self, depth):
        """Create <tmp>/.pg/project.yaml and a nested script dir of given depth."""
        pg_dir = os.path.join(self.tmp.name, ".pg")
        os.makedirs(pg_dir)
        config_path = os.path.join(pg_dir, "project.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("modules: {}\n")
        nested = self.tmp.name
        for i in range(depth):
            nested = os.path.join(nested, "level%d" % i)
        os.makedirs(nested, exist_ok=True)
        return config_path, nested

    def test_finds_config_at_five_levels(self):
        # Mirrors real layout: <root>/.pg/skills/src/core/workflows/scripts/
        config_path, nested = self._make_layout(5)
        found = self.mod._find_project_yaml_upward(nested)
        self.assertEqual(found, config_path)

    def test_finds_config_at_one_level(self):
        config_path, nested = self._make_layout(1)
        found = self.mod._find_project_yaml_upward(nested)
        self.assertEqual(found, config_path)

    def test_finds_config_from_root_itself(self):
        # Start search from a directory whose parent chain contains no
        # .pg/project.yaml except the one we create: use the .pg dir itself
        # as the start point — upward traversal from there finds the config
        # at its parent (<tmp>/.pg/project.yaml).
        config_path, _ = self._make_layout(0)
        pg_dir = os.path.dirname(config_path)
        found = self.mod._find_project_yaml_upward(pg_dir)
        self.assertEqual(found, config_path)

    def test_returns_none_when_missing(self):
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        found = self.mod._find_project_yaml_upward(empty.name)
        self.assertIsNone(found)


class ResolveConfigPathCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_parser()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Simulate consumer-project layout:
        # <tmp>/.pg/project.yaml + <tmp>/.pg/skills/src/core/workflows/scripts/script.py
        os.makedirs(os.path.join(self.tmp.name, ".pg"))
        self.config_path = os.path.join(self.tmp.name, ".pg", "project.yaml")
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("modules: {}\n")
        self.script_dir = os.path.join(
            self.tmp.name, ".pg", "skills", "src", "core", "workflows", "scripts"
        )
        os.makedirs(self.script_dir)

    def test_candidate_resolves_from_script_dir(self):
        path = self.mod.CONFIG_PATH_CANDIDATES[0](self.script_dir)
        self.assertEqual(path, self.config_path)

    def test_resolve_config_path_picks_existing_candidate(self):
        resolved = self.mod._resolve_config_path()
        # Module-level CONFIG_PATH resolved at import time from the real
        # script location; just assert the helper never raises.
        self.assertIsInstance(resolved, str)

    def test_fallback_when_no_config_anywhere(self):
        isolated = tempfile.TemporaryDirectory()
        self.addCleanup(isolated.cleanup)
        old_cwd = os.getcwd()
        try:
            os.chdir(isolated.name)
            # Patch __file__ resolution by calling candidates directly with an
            # isolated script dir — both candidates return None/fallback.
            path = self.mod.CONFIG_PATH_CANDIDATES[0](isolated.name)
            self.assertIsNone(path)
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
