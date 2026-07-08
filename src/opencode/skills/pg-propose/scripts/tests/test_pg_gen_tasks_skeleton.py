#!/usr/bin/env python3
"""Tests for pg-gen-tasks-skeleton.py — pure-function skeleton generator.

Each test case writes a mock .pg/project.yaml into a temp dir, invokes
the script via subprocess, and asserts on the generated tasks.md +
on-conditions-eval.md content + stdout JSON.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

import importlib.util as _ilu
_skel_spec = _ilu.spec_from_file_location(
    "pg_gen_tasks_skeleton",
    os.path.join(_SCRIPTS_DIR, "pg-gen-tasks-skeleton.py"),
)
assert _skel_spec is not None and _skel_spec.loader is not None
_skel_mod = _ilu.module_from_spec(_skel_spec)
_skel_spec.loader.exec_module(_skel_mod)
skel = _skel_mod


# ============================================================
# Pure-function unit tests (no I/O)
# ============================================================

class TestParseEnvMap(unittest.TestCase):

    def test_full_width_arrow(self):
        self.assertEqual(
            skel.parse_env_map("dev→dev-local"),
            {"dev": "dev-local"}
        )

    def test_ascii_arrow(self):
        self.assertEqual(
            skel.parse_env_map("dev->dev-local"),
            {"dev": "dev-local"}
        )

    def test_multiple_entries(self):
        result = skel.parse_env_map(
            "dev→dev-local, real-integration→multi-tier"
        )
        self.assertEqual(result, {
            "dev": "dev-local",
            "real-integration": "multi-tier",
        })

    def test_chinese_comma_separator(self):
        result = skel.parse_env_map("dev→dev-local，real-integration→multi-tier")
        self.assertEqual(result, {
            "dev": "dev-local",
            "real-integration": "multi-tier",
        })

    def test_whitespace_tolerance(self):
        result = skel.parse_env_map("  dev  →  dev-local  ,  prep  →  multi-tier ")
        self.assertEqual(result, {
            "dev": "dev-local",
            "prep": "multi-tier",
        })

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            skel.parse_env_map("malformed-no-arrow")


class TestExtractGlobsFromProposal(unittest.TestCase):

    def test_backtick_paths(self):
        text = "我们修改了 `webvirt-backend/src/main/java/Foo.java` 和 `pg-spec-deprecated/scripts/x.sh`"
        globs = skel.extract_globs_from_proposal(text)
        self.assertIn("webvirt-backend/src/main/java/Foo.java", globs)
        self.assertIn("pg-spec-deprecated/scripts/x.sh", globs)

    def test_bold_path_descriptions(self):
        text = "**foo**: `webvirt-frontend/src/views/X.vue` 用于新页面"
        globs = skel.extract_globs_from_proposal(text)
        self.assertTrue(any("webvirt-frontend/src/views/X.vue" in g for g in globs))

    def test_deduplication(self):
        text = "`a/b.sh` then `a/b.sh` again"
        globs = skel.extract_globs_from_proposal(text)
        self.assertEqual(globs.count("a/b.sh"), 1)


class TestExtractGlobsFromRule(unittest.TestCase):

    def test_single_glob(self):
        globs = skel.extract_globs_from_rule(
            "本变更 affected_paths 命中 .pg/hooks/** 任一路径"
        )
        self.assertIn(".pg/hooks/**", globs)

    def test_multiple_globs(self):
        globs = skel.extract_globs_from_rule(
            "本变更涉及 fixtures/ 和 scripts/ 下的修改"
        )
        self.assertIn("fixtures/", globs)
        self.assertIn("scripts/", globs)

    def test_no_glob(self):
        globs = skel.extract_globs_from_rule("本变更包含对环境层脚本的修改描述")
        self.assertEqual(globs, [])


class TestExtractKeywordsFromRule(unittest.TestCase):

    def test_filters_stop_phrases(self):
        kw = skel.extract_keywords_from_rule(
            "本变更 affected_paths 命中 fixtures 路径"
        )
        self.assertIn("fixtures", kw)
        self.assertIn("路径", kw)
        self.assertNotIn("本变更", kw)
        self.assertNotIn("命中", kw)

    def test_excludes_globs(self):
        kw = skel.extract_keywords_from_rule(
            "本变更涉及 .pg/hooks/** 路径"
        )
        self.assertNotIn(".pg/hooks/**", kw)

    def test_drops_single_char_tokens(self):
        kw = skel.extract_keywords_from_rule("涉及 A B 文件")
        self.assertNotIn("A", kw)
        self.assertNotIn("B", kw)


class TestCheckGlobMatch(unittest.TestCase):

    def test_exact_match(self):
        self.assertTrue(skel.check_glob_match(
            "命中 .pg/hooks/**",
            [".pg/hooks/setup.sh"]
        ))

    def test_double_star_prefix(self):
        self.assertTrue(skel.check_glob_match(
            "命中 pg-spec-deprecated/scripts/**",
            ["pg-spec-deprecated/scripts/fixtures/x.sql"]
        ))

    def test_no_match(self):
        self.assertFalse(skel.check_glob_match(
            "命中 .pg/hooks/**",
            ["webvirt-backend/src/main/java/Foo.java"]
        ))

    def test_empty_paths(self):
        self.assertFalse(skel.check_glob_match("命中 .pg/hooks/**", []))

    def test_empty_rule_globs(self):
        self.assertFalse(skel.check_glob_match(
            "本变更涉及 fixtures 修改", ["fixtures/x.sql"]
        ))


class TestCheckKeywordMatch(unittest.TestCase):

    def test_keyword_present(self):
        self.assertTrue(skel.check_keyword_match(
            "本变更包含对 setup 脚本注入的修改",
            "本次涉及 setup 脚本注入逻辑"
        ))

    def test_keyword_absent(self):
        self.assertFalse(skel.check_keyword_match(
            "本变更包含对 setup 脚本注入的修改",
            "本次只调整前端样式"
        ))

    def test_empty_proposal(self):
        self.assertFalse(skel.check_keyword_match("涉及 fixtures", ""))


# ============================================================
# Pure-function: build_sections
# ============================================================

class TestBuildSections(unittest.TestCase):

    def _config(self, stages, tracks):
        return {"stages": stages, "tracks": tracks}

    def test_single_standard_track_yields_5_sections_plus_final(self):
        """v3.x: default code_review_enabled=True → 5 sub sections + final-gate."""
        cfg = self._config(
            stages=[{
                "name": "dev", "test_key": "unit",
                "tracks": ["backend"],
                "environment": {"required": True},
            }],
            tracks={"backend": {"modules": ["backend"]}},
        )
        sections = skel.build_sections(cfg, {"backend"}, set())
        self.assertEqual(len(sections), 6)  # 5 sub + final-gate
        self.assertEqual(sections[0]["n"], 1)
        self.assertEqual(sections[-1]["track"], "final-gate")
        self.assertEqual(sections[-1]["n"], 6)
        # 5 sub names in order
        sub_names = [s["sub"] for s in sections[:-1]]
        self.assertEqual(
            sub_names,
            ["test", "dev", "code-view", "verify", "gate"],
        )

    def test_simple_track_yields_1_section(self):
        cfg = self._config(
            stages=[{
                "name": "dev", "test_key": "unit",
                "tracks": ["openapi-gen"],
                "environment": {"required": True},
            }],
            tracks={"openapi-gen": {"type": "simple", "commands": ["pnpm openapi"]}},
        )
        sections = skel.build_sections(cfg, {"openapi-gen"}, set())
        # simple track: 1 section + final-gate = 2
        self.assertEqual(len(sections), 2)
        self.assertTrue(sections[0]["is_simple"])
        self.assertEqual(sections[0]["n"], 1)
        self.assertEqual(sections[1]["n"], 2)

    def test_mixed_tracks_yields_correct_count(self):
        """v3.x: default code_review_enabled=True → 5 sub per standard track."""
        cfg = self._config(
            stages=[{
                "name": "dev", "test_key": "unit",
                "tracks": ["backend", "openapi-gen", "frontend"],
                "environment": {"required": True},
            }],
            tracks={
                "backend": {"modules": ["backend"]},
                "openapi-gen": {"type": "simple", "commands": ["pnpm openapi"]},
                "frontend": {"modules": ["frontend"]},
            },
        )
        sections = skel.build_sections(cfg, {"backend", "frontend"}, set())
        # openapi-gen 不在 affected_tracks → 被跳过
        # backend: 5 sections (含 code-view)
        # frontend: 5 sections
        # final-gate: 1
        # total: 11
        self.assertEqual(len(sections), 11)
        # numbering should be sequential 1..11
        self.assertEqual([s["n"] for s in sections], list(range(1, 12)))
        # is_affected flags
        self.assertTrue(all(s["is_affected"] for s in sections
                            if s["track"] == "backend"))
        self.assertTrue(all(s["is_affected"] for s in sections
                            if s["track"] == "frontend"))
        # openapi-gen (simple): is_affected always False regardless of input
        openapi_sections = [s for s in sections if s["track"] == "openapi-gen"]
        self.assertTrue(all(s["is_affected"] is False for s in openapi_sections))

    def test_affected_tracks_set_filters_body_not_headings(self):
        cfg = self._config(
            stages=[{
                "name": "dev", "test_key": "unit",
                "tracks": ["backend", "frontend"],
                "environment": {"required": True},
            }],
            tracks={
                "backend": {"modules": ["backend"]},
                "frontend": {"modules": ["frontend"]},
            },
        )
        # only backend is affected
        sections = skel.build_sections(cfg, {"backend"}, set())
        backend_sections = [s for s in sections if s["track"] == "backend"]
        frontend_sections = [s for s in sections if s["track"] == "frontend"]
        # v3.x: backend (affected) 5 sections; frontend (not affected) 0 sections (skipped)
        self.assertEqual(len(backend_sections), 5)
        self.assertEqual(len(frontend_sections), 0)
        # backend affected → True
        self.assertTrue(all(s["is_affected"] for s in backend_sections))

    def test_no_stages_returns_just_final_gate(self):
        cfg = self._config(stages=[], tracks={})
        sections = skel.build_sections(cfg, set(), set())
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["track"], "final-gate")


# ============================================================
# Pure-function: format_env_block_quote
# ============================================================

class TestFormatEnvBlockQuote(unittest.TestCase):

    def test_single_entry(self):
        self.assertEqual(
            skel.format_env_block_quote({"dev": "dev-local"}),
            "> - **environment 选择**：dev → dev-local"
        )

    def test_multiple_entries(self):
        result = skel.format_env_block_quote({
            "dev": "dev-local",
            "real-integration": "multi-tier",
        })
        self.assertIn("dev → dev-local", result)
        self.assertIn("real-integration → multi-tier", result)

    def test_empty(self):
        self.assertEqual(skel.format_env_block_quote({}), "")


# ============================================================
# Pure-function: format_section_body
# ============================================================

class TestFormatSectionBody(unittest.TestCase):

    def test_unaffected_standard(self):
        sec = {"n": 5, "stage": "dev", "track": "frontend", "sub": "dev",
               "is_simple": False, "is_affected": False}
        self.assertEqual(skel.format_section_body(sec), "- 无")

    def test_affected_test(self):
        sec = {"n": 2, "stage": "dev", "track": "backend", "sub": "test",
               "is_simple": False, "is_affected": True}
        body = skel.format_section_body(sec)
        self.assertIn("- [ ] 2.1", body)
        self.assertIn("测试", body)

    def test_affected_dev(self):
        sec = {"n": 3, "stage": "dev", "track": "backend", "sub": "dev",
               "is_simple": False, "is_affected": True}
        body = skel.format_section_body(sec)
        self.assertIn("- [ ] 3.1", body)

    def test_affected_verify_has_4_tasks(self):
        sec = {"n": 4, "stage": "dev", "track": "backend", "sub": "verify",
               "is_simple": False, "is_affected": True}
        body = skel.format_section_body(sec)
        # verify has lint / test / start / V-* (4 tasks)
        self.assertIn("- [ ] 4.1", body)
        self.assertIn("- [ ] 4.2", body)
        self.assertIn("- [ ] 4.3", body)
        self.assertIn("- [ ] 4.4", body)
        self.assertIn("V-backend-", body)

    def test_gate_is_always_noop(self):
        sec = {"n": 5, "stage": "dev", "track": "backend", "sub": "gate",
               "is_simple": False, "is_affected": True}
        body = skel.format_section_body(sec)
        self.assertEqual(body, "- 无")

    def test_simple_track_placeholder(self):
        sec = {"n": 9, "stage": "dev", "track": "openapi-gen", "sub": None,
               "is_simple": True, "is_affected": False}
        body = skel.format_section_body(sec)
        self.assertIn("tracks.openapi-gen.commands", body)
        self.assertIn("pg-build/simple", body)

    def test_final_gate_3_tasks(self):
        sec = {"n": 26, "stage": "final", "track": "final-gate", "sub": None,
               "is_simple": False, "is_affected": False}
        body = skel.format_section_body(sec)
        self.assertIn("- [ ] 26.1", body)
        self.assertIn("- [ ] 26.2", body)
        self.assertIn("- [ ] 26.3", body)


# ============================================================
# Pure-function: build_tasks_md (full integration, no I/O)
# ============================================================

class TestBuildTasksMd(unittest.TestCase):

    def _minimal_config(self):
        return {
            "stages": [{
                "name": "dev", "test_key": "unit",
                "tracks": ["backend", "frontend"],
                "environment": {"required": True},
            }],
            "tracks": {
                "backend": {"modules": ["backend"]},
                "frontend": {"modules": ["frontend"]},
            },
        }

    def test_top_block_quote_present(self):
        cfg = self._minimal_config()
        sections = skel.build_sections(cfg, {"backend"}, set())
        text = skel.build_tasks_md(
            sections, {"dev": "dev-local"}, cfg, [], ""
        )
        self.assertIn("> - **environment 选择**：dev → dev-local", text)

    def test_all_sections_present_no_skipping(self):
        cfg = self._minimal_config()
        sections = skel.build_sections(cfg, {"backend", "frontend"}, set())
        text = skel.build_tasks_md(
            sections, {"dev": "dev-local"}, cfg, [], ""
        )
        # backend (5, 含 code-view) + frontend (5, 含 code-view) + final-gate (1) = 11 headings
        self.assertEqual(text.count("## "), 11)

    def test_unaffected_body_is_noop(self):
        """v3.x: 不在 affected_tracks 的 track → 章节不生成（v2.6 行为是 body=-无，v3.x 改为不生成）。
        
        行为差异：v2.6 保留 heading 写 - 无；v3.x 直接跳过。理由：减少 tasks.md 噪声，
        runner 已知道哪些 track 不需要执行（pipeline_order 不含）。
        """
        cfg = self._minimal_config()
        sections = skel.build_sections(cfg, {"backend"}, set())
        text = skel.build_tasks_md(
            sections, {"dev": "dev-local"}, cfg, [], ""
        )
        # frontend (not affected) → 不出现在 sections，text 中没有 frontend 章节
        self.assertNotIn("dev.frontend", text)
        # backend (affected) → 5 sub
        self.assertIn("## 1. dev.backend:test", text)
        self.assertIn("## 2. dev.backend:dev", text)
        self.assertIn("## 3. dev.backend:code-view", text)
        self.assertIn("## 4. dev.backend:verify", text)
        self.assertIn("## 5. dev.backend:gate", text)

    def test_affected_body_has_tasks(self):
        cfg = self._minimal_config()
        sections = skel.build_sections(cfg, {"backend", "frontend"}, set())
        text = skel.build_tasks_md(
            sections, {"dev": "dev-local"}, cfg, [], ""
        )
        # backend dev section should NOT contain "- 无"
        # find the backend dev section
        bk_dev_start = text.index("dev.backend:dev")
        bk_dev_end = text.index("dev.backend:verify")
        bk_dev_chunk = text[bk_dev_start:bk_dev_end]
        self.assertNotIn("- 无", bk_dev_chunk)
        self.assertIn("- [ ] 2.1", bk_dev_chunk)

    def test_final_gate_present(self):
        cfg = self._minimal_config()
        sections = skel.build_sections(cfg, {"backend"}, set())
        text = skel.build_tasks_md(
            sections, {"dev": "dev-local"}, cfg, [], ""
        )
        # v3.x: backend (5 sub) + final-gate → N=6
        self.assertIn("## 6. final-gate", text)
        self.assertIn("Gate Assessment", text)

    def test_on_conditions_comment_present(self):
        cfg = {
            "stages": [{
                "name": "prep", "test_key": "unit",
                "tracks": ["env"],
                "environment": {"required": False},
                "on_conditions": [
                    "本变更 affected_paths 命中 .pg/hooks/** 任一路径"
                ],
            }],
            "tracks": {"env": {"modules": ["env"]}},
        }
        sections = skel.build_sections(cfg, {"env"}, set())
        text = skel.build_tasks_md(
            sections, {}, cfg,
            [".pg/hooks/setup.sh"], ""
        )
        self.assertIn("<!-- on_conditions_eval:", text)
        self.assertIn("stage=prep", text)
        self.assertIn(".pg/hooks/**", text)
        self.assertIn("命中", text)


# ============================================================
# Pure-function: build_on_conditions_eval_md
# ============================================================

class TestBuildOnConditionsEvalMd(unittest.TestCase):

    def test_stage_with_no_rules_skipped(self):
        cfg = {
            "stages": [{
                "name": "dev", "test_key": "unit", "tracks": ["backend"],
                "environment": {"required": True},
            }],
            "tracks": {"backend": {"modules": ["backend"]}},
        }
        text = skel.build_on_conditions_eval_md(cfg, [], "")
        # no stage has on_conditions → no stage-level subsection
        self.assertNotIn("### dev", text)
        self.assertIn("## stage 级", text)

    def test_stage_with_rules_table_format(self):
        cfg = {
            "stages": [{
                "name": "prep", "test_key": "unit", "tracks": ["env"],
                "environment": {"required": False},
                "on_conditions": [
                    "本变更 affected_paths 命中 .pg/hooks/**",
                    "本变更包含 fixtures 修改",
                ],
            }],
            "tracks": {"env": {"modules": ["env"]}},
        }
        text = skel.build_on_conditions_eval_md(
            cfg, [".pg/hooks/setup.sh"], "包含 fixtures 修改"
        )
        self.assertIn("### prep", text)
        self.assertIn("| 1 |", text)
        self.assertIn("| 2 |", text)
        self.assertIn("✅", text)

    def test_track_with_rules_table_format(self):
        cfg = {
            "stages": [{
                "name": "dev", "test_key": "unit", "tracks": ["openapi-gen"],
                "environment": {"required": True},
            }],
            "tracks": {
                "openapi-gen": {
                    "type": "simple", "commands": ["x"],
                    "on_conditions": ["本变更涉及 OpenAPI 注解"],
                },
            },
        }
        text = skel.build_on_conditions_eval_md(
            cfg, [], "本次修改了 OpenAPI 注解"
        )
        self.assertIn("## track 级", text)
        self.assertIn("### openapi-gen", text)
        self.assertIn("✅", text)


# ============================================================
# End-to-end CLI test (subprocess)
# ============================================================

class TestCliE2E(unittest.TestCase):
    """Run the script as a subprocess against a mock project layout."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.proposal_path = os.path.join(self.tmpdir, "proposal.md")
        self.config_path = os.path.join(self.tmpdir, ".pg", "project.yaml")
        self.change = "test-change"
        self.change_dir = os.path.join(self.tmpdir, ".pg", "changes", self.change)
        os.makedirs(os.path.join(self.tmpdir, ".pg", "changes"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, ".pg", "changes", self.change, "1-propose-review"), exist_ok=True)

    def _write_project_yaml(self, yaml_text):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)

    def _write_proposal(self, text):
        with open(self.proposal_path, "w", encoding="utf-8") as f:
            f.write(text)

    def _run(self, args):
        cmd = [
            sys.executable,
            os.path.join(_SCRIPT_DIR, "..", "pg-gen-tasks-skeleton.py"),
        ] + args + [
            "--change", self.change,
            "--proposal-md", self.proposal_path,
        ]
        env = os.environ.copy()
        env["PG_PROJECT_ROOT"] = self.tmpdir
        return subprocess.run(
            cmd, capture_output=True, text=True, env=env, cwd=self.tmpdir
        )

    def test_minimal_standard_track(self):
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - name: dev
                tracks: [backend]
                test_key: unit
                environment:
                  required: true
            tracks:
              backend:
                modules: [backend]
        """))
        self._write_proposal("Basic proposal text.")
        result = self._run([
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        # v3.x: backend (5 sub, 含 code-view) + final-gate = 6
        self.assertEqual(json_out["section_count"], 6)
        tasks_path = json_out["tasks_md_written"]
        self.assertTrue(os.path.isfile(tasks_path))
        with open(tasks_path) as f:
            text = f.read()
        self.assertIn("> - **environment 选择**：dev → dev-local", text)
        self.assertIn("## 1. dev.backend:test", text)
        self.assertIn("## 3. dev.backend:code-view", text)
        self.assertIn("## 6. final-gate", text)

    def test_simple_track(self):
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - name: dev
                tracks: [openapi-gen]
                test_key: unit
                environment:
                  required: true
            tracks:
              openapi-gen:
                type: simple
                commands: ["pnpm openapi"]
        """))
        self._write_proposal("Basic proposal.")
        result = self._run([
            "--affected-tracks", "openapi-gen",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        # simple (1) + final-gate (1) = 2
        self.assertEqual(json_out["section_count"], 2)
        tasks_path = json_out["tasks_md_written"]
        with open(tasks_path) as f:
            text = f.read()
        self.assertIn("## 1. dev.openapi-gen", text)
        self.assertIn("tracks.openapi-gen.commands", text)

    def test_on_conditions_with_glob_hit(self):
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - name: prep
                tracks: [env]
                test_key: unit
                environment:
                  required: false
                on_conditions:
                  - "本变更 affected_paths 命中 .pg/hooks/** 任一路径"
            tracks:
              env:
                modules: [env]
        """))
        self._write_proposal(
            "修改文件 `.pg/hooks/setup.sh`，添加新脚本"
        )
        result = self._run([
            "--affected-tracks", "env",
            "--environment", "",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        json_out = json.loads(result.stdout)
        eval_path = json_out["on_conditions_eval_written"]
        self.assertTrue(os.path.isfile(eval_path))
        with open(eval_path) as f:
            text = f.read()
        self.assertIn("### prep", text)
        self.assertIn("✅", text)  # glob hit

    def test_on_conditions_keyword_hit(self):
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - name: prep
                tracks: [env]
                test_key: unit
                environment:
                  required: false
                on_conditions:
                  - "本变更包含对环境层脚本或 fixtures 的修改描述"
            tracks:
              env:
                modules: [env]
        """))
        self._write_proposal(
            "本次修改了环境层脚本，新增 setup 脚本"
        )
        result = self._run([
            "--affected-tracks", "env",
            "--environment", "",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        with open(json.loads(result.stdout)["on_conditions_eval_written"]) as f:
            text = f.read()
        self.assertIn("✅", text)  # keyword hit

    def test_unaffected_chapter_body_is_noop(self):
        """v3.x: 不在 affected_tracks 的 track → 章节不生成（v2.6 行为是 body=-无）。"""
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - name: dev
                tracks: [backend, frontend]
                test_key: unit
                environment:
                  required: true
            tracks:
              backend: {modules: [backend]}
              frontend: {modules: [frontend]}
        """))
        self._write_proposal("Backend-only change.")
        result = self._run([
            "--affected-tracks", "backend",
            "--environment", "dev→dev-local",
        ])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        tasks_path = json.loads(result.stdout)["tasks_md_written"]
        with open(tasks_path) as f:
            text = f.read()
        # v3.x: frontend 不在 affected_tracks → 章节不生成
        self.assertNotIn("dev.frontend", text)
        # backend 5 sub 全在
        self.assertIn("## 1. dev.backend:test", text)
        self.assertIn("## 3. dev.backend:code-view", text)
        self.assertIn("## 5. dev.backend:gate", text)

    def test_invalid_environment_arg_exits_nonzero(self):
        self._write_project_yaml(textwrap.dedent("""
            stages:
              - {name: dev, tracks: [b], test_key: unit, environment: {required: true}}
            tracks:
              b: {modules: [b]}
        """))
        self._write_proposal("test")
        result = self._run([
            "--affected-tracks", "b",
            "--environment", "malformed-no-arrow",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法解析", result.stderr)


if __name__ == "__main__":
    unittest.main()