"""v3.7 placeholder 校验 + auto-refine 检测单测.

覆盖:
- pg-gen-scenario.py: check_scenario_placeholders 占位符检测（全填/部分填/未填）
- pg-auto-refine-check.py: 全推荐 / 有 SKIP / 已编辑 三场景
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest


_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_scenario = _load("pg_gen_scenario", f"{_SCRIPTS_DIR}/pg-gen-scenario.py")
_ARC = _load("pg_auto_refine_check", f"{_SCRIPTS_DIR}/pg-auto-refine-check.py")
# validator.py loads pg-gen-scenario internally; ensure no name conflict
sys.path.insert(0, _SCRIPTS_DIR)

# 注册到 sys.modules 以支持 importlib.reload
sys.modules["pg_auto_refine_check"] = _ARC

# ============================================================
# check_scenario_placeholders pure-function tests
# ============================================================

class TestCheckScenarioPlaceholders(unittest.TestCase):
    """placeholder 检测 — 元件级（直接调用 check_scenario_placeholders）。"""

    def test_fully_filled_returns_empty(self):
        """LLM 已替换所有占位符 → 无 issue."""
        doc = {
            "scenarios": [
                {
                    "scenario_id": "S-create-bucket-success",
                    "description": "创建存储桶成功",
                    "given": ["无", "已登录"],
                    "when": [{
                        "name": "create",
                        "method": "POST",
                        "url": "/api/iam.webvirt/v3/buckets",
                        "expect_status": 201,
                    }],
                    "then": ["status_code == 201"],
                    "and": [{"name": "cleanup", "action": "HTTP DELETE"}],
                    "evidence": ["./evidence.json"],
                }
            ]
        }
        issues = _scenario.check_scenario_placeholders(doc)
        self.assertEqual([], issues, f"unexpected: {issues}")

    def test_unfilled_skeleton_has_many_issues(self):
        """全新 skeleton 全部含占位符 → 多 issue."""
        # 直接复制 _build_skeleton_yaml 的输出
        doc = _scenario._build_skeleton_yaml("test-change", "scenario-test")
        issues = _scenario.check_scenario_placeholders(doc)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_placeholder_unfilled", codes)
        # 至少 5 类占位符（scenario_id, description, given, when.url, then）
        self.assertGreaterEqual(len(issues), 5)

    def test_partial_fill_only_partial_issues(self):
        """部分填充的 scenario 报告对应未填充段."""
        doc = {
            "scenarios": [
                {
                    "scenario_id": "S-create-bucket",  # 已填
                    "description": "<一句话描述此 Scenario 验证目标（LLM 必填）>",  # 占位符
                    "given": ["无"],  # 已填
                    "when": [{
                        "name": "create",
                        "method": "POST",
                        "url": "/api/iam.../buckets",  # 占位符
                        "expect_status": 201,
                    }],
                    "then": ["status_code == 201"],  # 已填
                    "and": [{"name": "cleanup", "action": "HTTP DELETE"}],  # 已填
                    "evidence": ["./e.json"],
                }
            ]
        }
        issues = _scenario.check_scenario_placeholders(doc)
        codes = [c for c, _ in issues]
        msgs = " | ".join(m for _, m in issues)
        self.assertIn("scenario_placeholder_unfilled", codes)
        self.assertIn("description", msgs)
        self.assertIn("when[0].url", msgs)
        # 其他字段不应报错
        self.assertNotIn("scenario_id", msgs)
        self.assertNotIn("given[", msgs)

    def test_empty_scenarios_list_reports_error(self):
        """scenarios 为空数组 → 占位符校验失败（必填）."""
        doc = {"scenarios": []}
        issues = _scenario.check_scenario_placeholders(doc)
        codes = [c for c, _ in issues]
        self.assertIn("scenario_placeholder_unfilled", codes)

    def test_non_dict_top_reports_error(self):
        """顶层不是 dict → 报错."""
        for bad in ([], "string", 42, None):
            issues = _scenario.check_scenario_placeholders(bad)
            codes = [c for c, _ in issues]
            self.assertIn("scenario_placeholder_unfilled", codes)


class TestIsPlaceholderString(unittest.TestCase):
    """_is_placeholder_string 检测单个字符串。"""

    def test_angle_brackets_detected(self):
        self.assertTrue(_scenario._is_placeholder_string("<foo bar>"))
        self.assertTrue(_scenario._is_placeholder_string("<中文描述>"))
        self.assertTrue(_scenario._is_placeholder_string("<动作名>"))

    def test_url_with_ellipsis_detected(self):
        self.assertTrue(_scenario._is_placeholder_string("/api/iam.../buckets"))
        self.assertTrue(_scenario._is_placeholder_string("/api/iam.webvirt.../v3/buckets"))

    def test_S_prefix_undetected(self):
        self.assertTrue(_scenario._is_placeholder_string("S-<unique-name>"))
        # 不应以 "S-" 开头的被视为占位符（真实 S-list-bucket 等无 < >）
        self.assertFalse(_scenario._is_placeholder_string("S-list-bucket-success"))

    def test_llm_required_marker_detected(self):
        self.assertTrue(_scenario._is_placeholder_string("description（LLM 必填）"))
        self.assertTrue(_scenario._is_placeholder_string("（LLM 必填） description"))

    def test_real_text_not_detected(self):
        self.assertFalse(_scenario._is_placeholder_string("GET /api/iam/buckets"))
        self.assertFalse(_scenario._is_placeholder_string("创建 bucket 成功"))
        self.assertFalse(_scenario._is_placeholder_string("response.id matches '[a-f0-9]{32}'"))

    def test_report_seq_runtime_placeholder_not_detected(self):
        """v3.8: <report_seq> 是运行时注入占位符，不该被 LLM 替换也不该被检测为占位符.
        scenario.yaml evidence 字段写 `2-build/<report_seq>-<scenario_id>-evidence.json`，
        LLM 填充后保留 <report_seq>，由 pg-build 编排器在 dispatch 时替换.
        """
        # 仅含 <report_seq> → 不该被检测为占位符
        self.assertFalse(_scenario._is_placeholder_string("2-build/<report_seq>-S-foo-evidence.json"))
        # 含 <scenario_id> + <report_seq> → 仍含 LLM 必填占位符，需报告
        self.assertTrue(_scenario._is_placeholder_string("2-build/<report_seq>-<scenario_id>-evidence.json"))
        # 含 <report_seq> + 其他占位符（如 <动作名>）→ 仍需报告
        self.assertTrue(_scenario._is_placeholder_string("<report_seq>-<动作名>-foo"))


class TestCheckScenarioFile(unittest.TestCase):
    """check_scenario_file 测试 — 文件级别封装."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_file_does_not_exist(self):
        # write yaml to nowhere should error
        result = _scenario.check_scenario_file("/tmp/__nonexistent_yaml__.yaml")
        codes = [c for c, _ in result]
        self.assertIn("scenario_placeholder_unfilled", codes)

    def test_filled_yaml_passes(self):
        path = os.path.join(self.tmpdir, "scenario-test.yaml")
        with open(path, "w") as f:
            f.write("""
scenarios:
  - scenario_id: S-test
    description: 测试场景
    given:
      - 已登录
    when:
      - name: x
        method: GET
        url: /api/x
        expect_status: 200
    then:
      - status_code == 200
    and:
      - name: cleanup
        action: HTTP DELETE
    evidence:
      - ./e.json
""")
        self.assertEqual([], _scenario.check_scenario_file(path))

    def test_filled_yaml_with_url_placeholder_fails(self):
        path = os.path.join(self.tmpdir, "scenario-test.yaml")
        with open(path, "w") as f:
            f.write("""
scenarios:
  - scenario_id: S-test
    description: 测试场景
    given:
      - 已登录
    when:
      - name: x
        method: GET
        url: /api/iam.../users
        expect_status: 200
    then:
      - status_code == 200
    and:
      - name: cleanup
        action: HTTP DELETE
    evidence:
      - ./e.json
""")
        codes = [c for c, _ in _scenario.check_scenario_file(path)]
        self.assertIn("scenario_placeholder_unfilled", codes)


# ============================================================
# pg-auto-refine-check.py tests
# ============================================================

_ARC = _load("pg_auto_refine_check", f"{_SCRIPTS_DIR}/pg-auto-refine-check.py")


class TestAutoRefineCheck(unittest.TestCase):
    """条件检测 — 全推荐 / 有 SKIP / 已编辑 / 缺文件 四场景。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, ".pg", "changes"), exist_ok=True)
        # 直接 monkey-patch 模块级 CHANGES_DIR
        self._old_changes_dir = getattr(_ARC, "CHANGES_DIR", None)
        _ARC.CHANGES_DIR = os.path.join(self.tmpdir, ".pg", "changes")

    def tearDown(self):
        if self._old_changes_dir is not None:
            _ARC.CHANGES_DIR = self._old_changes_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_change(self, change: str, review_content: str):
        change_dir = os.path.join(self.tmpdir, ".pg", "changes", change)
        os.makedirs(os.path.join(change_dir, "1-propose-review"), exist_ok=True)
        with open(os.path.join(change_dir, "1-propose-review", "review-notes.md"), "w") as f:
            f.write(review_content)

    def test_all_recommended_should_auto_apply(self):
        """所有 5 项 common_decisions current==recommended，无 [~] → 自动应用。"""
        review = """# Test Review

**生成时间**：2026-07-15
**自审依据**：test

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | A | A | 沿用 |
| auth_scope | platform / tenant / project | platform | platform | 单一 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 无 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 纯查询 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 沿用 |

## 自审发现的问题

### 阻塞（必须修复后再 build）
- [ ] （无）

### 重要（建议修复后再 build）
- [ ] （无）

### 建议（可选优化）
- [ ] （无）
"""
        self._write_change("test-all-rec", review)
        r = _ARC.check_should_auto_apply("test-all-rec")
        self.assertTrue(r["should_auto_apply"], msg=r)
        self.assertEqual(r["common_decisions_count"], 5)
        self.assertEqual(r["common_decisions_status"], "all_recommended")
        self.assertEqual(r["issue_decisions_status"], "all_default")
        self.assertFalse(r["user_edited"])

    def test_diverged_common_should_not_auto(self):
        """某 common_decision current!=recommended → 不自动。"""
        review = """# Test

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | B | A | 用户改 B |
| auth_scope | platform / tenant / project | platform | platform | 默认 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 默认 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 默认 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 默认 |

## 自审发现的问题

### 阻塞（必须修复后再 build）
- [ ] （无）

### 重要（建议修复后再 build）
- [ ] （无）

### 建议（可选优化）
- [ ] （无）
"""
        self._write_change("test-diverged", review)
        r = _ARC.check_should_auto_apply("test-diverged")
        self.assertFalse(r["should_auto_apply"])
        self.assertIn("diverged", r["common_decisions_status"])
        self.assertIn("error_response_strategy", r["common_decisions_status"])

    def test_user_skip_should_not_auto(self):
        """用户在 issue 列表打 [~] → 不自动（用户已表达 SKIP 意图）。"""
        review = """# Test

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | A | A | 默认 |
| auth_scope | platform / tenant / project | platform | platform | 默认 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 默认 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 默认 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 默认 |

## 自审发现的问题

### 阻塞（必须修复后再 build）
- [ ] （无）

### 重要（建议修复后再 build）
- [~] **tasks.md token 获取缺失**
  - 目标：tasks.md dev
  - 推荐动作：补充 token
  - SKIP 允许：是

### 建议（可选优化）
- [ ] （无）
"""
        self._write_change("test-user-skip", review)
        r = _ARC.check_should_auto_apply("test-user-skip")
        self.assertFalse(r["should_auto_apply"])
        self.assertEqual(r["issue_decisions_status"], "user_overrides")

    def test_user_edited_marker_should_not_auto(self):
        """通用决策被勾过 ✅ → current!=recommended 直接触发 'diverged'，不自动。"""
        review = """# Test

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | A ✅ | A | 已应用 |
| auth_scope | platform / tenant / project | platform | platform | 默认 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 默认 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 默认 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 默认 |

## 自审发现的问题

### 阻塞（必须修复后再 build）
- [ ] （无）

### 重要（建议修复后再 build）
- [ ] （无）

### 建议（可选优化）
- [ ] （无）
"""
        self._write_change("test-edited", review)
        r = _ARC.check_should_auto_apply("test-edited")
        # current='A ✅' 与 recommended='A' 不匹配 → common 触发分歧
        self.assertFalse(r["should_auto_apply"])
        self.assertIn("diverged", r["common_decisions_status"])
        self.assertIn("error_response_strategy", r["common_decisions_status"])

    def test_already_applied_marker_should_not_auto(self):
        """review-notes.md 已包含 '已应用时间' 文本 → 已 refine 过，不二次自动。"""
        review = """# Test

**已应用时间**：2026-07-15T10:00:00

## 通用决策（5 项骨架）

| 决策项 | 选项 | 当前 | 推荐 | 备注 |
|--------|------|------|------|------|
| error_response_strategy | A 全局统一 / B 按模块 | A | A | 默认 |
| auth_scope | platform / tenant / project | platform | platform | 默认 |
| data_migration_strategy | A Flyway / B 应用层兼容 / C 无需迁移 | C | C | 默认 |
| transaction_boundary | A 单 service @Transactional / B 分布式 / C 最终一致 | C | C | 默认 |
| frontend_interaction_style | A 弹窗 / B 抽屉 / C 独立页 / D 行内编辑 | B | B | 默认 |

## 自审发现的问题
- （无）
"""
        self._write_change("test-already-applied", review)
        r = _ARC.check_should_auto_apply("test-already-applied")
        self.assertFalse(r["should_auto_apply"])
        self.assertTrue(r["user_edited"])

    def test_no_review_notes_returns_error(self):
        """review-notes.md 不存在 → exit_code 2 + error 字段。"""
        os.makedirs(
            os.path.join(self.tmpdir, ".pg", "changes", "no-review", "1-propose-review"),
            exist_ok=True,
        )
        r = _ARC.check_should_auto_apply("no-review")
        self.assertFalse(r["should_auto_apply"])
        self.assertIn("error", r)
        self.assertEqual(r["exit_code"], 2)


# ============================================================
# v3.10 scenario 覆盖度校验 — parse_design_v_count / check_scenario_coverage / dynamic skeleton
# ============================================================


class TestParseDesignVCount(unittest.TestCase):
    """parse_design_v_count: 从 design.md ## Verification Criteria 段数 V-* 行."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        # mock CHANGES_DIR via monkey-patch
        self._orig_changes_dir = _scenario.CHANGES_DIR
        _scenario.CHANGES_DIR = self.tmpdir
        self.addCleanup(lambda: setattr(_scenario, "CHANGES_DIR", self._orig_changes_dir))

    def _write_design(self, change: str, content: str):
        os.makedirs(os.path.join(self.tmpdir, change), exist_ok=True)
        with open(os.path.join(self.tmpdir, change, "design.md"), "w", encoding="utf-8") as f:
            f.write(content)

    def test_no_design_md_returns_zero(self):
        r = _scenario.parse_design_v_count("no-design-change")
        self.assertEqual(r, 0)

    def test_no_verification_section_returns_zero(self):
        self._write_design("c1", "## 架构\n\n无 Verification Criteria 段。\n")
        r = _scenario.parse_design_v_count("c1")
        self.assertEqual(r, 0)

    def test_counts_all_v_id_rows(self):
        """含 Verification Criteria 段及多行 V-*-N 表格 → 返回行数."""
        design = (
            "## Verification Criteria\n"
            "\n"
            "### dev backend Verification Criteria\n"
            "\n"
            "| ID | 验证项 |\n"
            "|-----|--------|\n"
            "| V-backend-1 | a |\n"
            "| V-backend-2 | b |\n"
            "| V-backend-3 | c |\n"
            "\n"
            "### dev frontend Verification Criteria\n"
            "\n"
            "| ID | 验证项 |\n"
            "|-----|--------|\n"
            "| V-frontend-1 | x |\n"
            "| V-frontend-2 | y |\n"
            "\n"
            "### int backend Verification Criteria\n"
            "\n"
            "| ID | 验证项 |\n"
            "|-----|--------|\n"
            "| V-backend-int-1 | m |\n"
        )
        self._write_design("c2", design)
        r = _scenario.parse_design_v_count("c2")
        self.assertEqual(r, 6)


class TestCheckScenarioCoverage(unittest.TestCase):
    """check_scenario_coverage: 维度/数量/类型/covers warning-only 校验."""

    def _mk_doc(self, scenarios: list) -> dict:
        return {"scenarios": scenarios}

    def _mk_api_scenario(self, sid: str, expect_status: int = 200, covers=None, critical: bool = True) -> dict:
        sc = {
            "scenario_id": sid,
            "critical": critical,
            "description": f"{sid} desc",
            "given": [f"given for {sid}"],
            "when": [
                {"name": "call api", "type": "api", "method": "GET",
                 "url": "/api/example/webvirt.pangee.cmit.com/v3/things", "expect_status": expect_status},
            ],
            "then": [f"status_code == {expect_status}"],
            "and": [],
            "evidence": [f"2-build/x-{sid}-evidence.json"],
        }
        if covers is not None:
            sc["covers"] = covers
        return sc

    def _mk_browser_scenario(self, sid: str, covers=None) -> dict:
        sc = {
            "scenario_id": sid,
            "critical": False,
            "description": f"{sid} desc",
            "given": [f"given for {sid}"],
            "when": [
                {"name": "导航", "type": "browser", "action": "navigate", "url": "/page"},
                {"name": "截图", "type": "browser", "action": "screenshot"},
            ],
            "then": ["dom: body exists"],
            "and": [],
            "evidence": [f"2-build/x-{sid}-evidence.json"],
        }
        if covers is not None:
            sc["covers"] = covers
        return sc

    def test_minimal_one_scenario_warns_dimension_and_count(self):
        doc = self._mk_doc([self._mk_api_scenario("S-one")])
        issues = _scenario.check_scenario_coverage(doc, v_count=5, design_mentions_frontend=False)
        codes = [i[0] for i in issues]
        self.assertIn("scenario_coverage_dimension_missing", codes)
        self.assertIn("scenario_coverage_count_below_min", codes)

    def test_3_api_no_browser_with_frontend_design_warns_type(self):
        doc = self._mk_doc([self._mk_api_scenario(f"S-api-{i}") for i in range(3)])
        issues = _scenario.check_scenario_coverage(doc, v_count=4, design_mentions_frontend=True)
        codes = [i[0] for i in issues]
        self.assertIn("scenario_coverage_type_imbalance", codes)

    def test_full_coverage_no_warnings(self):
        """5 维度全覆盖 + 数量达标 + api+browser 都有 + covers 全填 → 无 issue."""
        scs = [
            self._mk_api_scenario("S-happy", 200, covers=["V-1"], critical=True),
            self._mk_api_scenario("S-negative-404", 404, covers=["V-2"], critical=False),
            self._mk_api_scenario("S-permission-403", 403, covers=["V-3"], critical=False),
            self._mk_api_scenario("S-cross-module", 200, covers=["V-4"], critical=False),
            self._mk_browser_scenario("S-ui-smoke", covers=["V-5"]),
        ]
        doc = self._mk_doc(scs)
        issues = _scenario.check_scenario_coverage(doc, v_count=5, design_mentions_frontend=True)
        self.assertEqual(issues, [])

    def test_below_min_count_includes_uncovered_v_list(self):
        """v_count=10 但只 2 scenario → issue message 含'未覆盖 V-*'提示."""
        doc = self._mk_doc([self._mk_api_scenario("S-only")])
        issues = _scenario.check_scenario_coverage(
            doc, v_count=10, design_mentions_frontend=True,
        )
        msgs = [i[1] for i in issues if i[0] == "scenario_coverage_count_below_min"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("V-", msgs[0])

    def test_covers_unset_warns(self):
        """covers 字段缺失 → warning."""
        sc = self._mk_api_scenario("S-no-covers")  # _mk_api_scenario 仅在 covers 不是 None 时才设置
        doc = self._mk_doc([sc])
        issues = _scenario.check_scenario_coverage(
            doc, v_count=3, design_mentions_frontend=False,
        )
        codes = [i[0] for i in issues]
        self.assertIn("scenario_coverage_covers_unset", codes)

    def test_critical_too_many_warns(self):
        """critical=true 超过 3 个 → warning."""
        scs = [self._mk_api_scenario(f"S-c{i}", critical=True) for i in range(5)]
        doc = self._mk_doc(scs)
        issues = _scenario.check_scenario_coverage(
            doc, v_count=5, design_mentions_frontend=False,
        )
        codes = [i[0] for i in issues]
        self.assertIn("scenario_coverage_critical_overflow", codes)


class TestDynamicSkeletonGeneration(unittest.TestCase):
    """_build_skeleton_yaml: v_count → scenario 数量动态派生."""

    def test_no_v_count_default_3(self):
        skel = _scenario._build_skeleton_yaml("c1", "scr")
        # 默认 + browser 模板 → 至少 2
        self.assertGreaterEqual(len(skel["scenarios"]), 2)

    def test_v_count_8_yields_6_scenarios(self):
        skel = _scenario._build_skeleton_yaml("c2", "scr", v_count=8)
        # max(3, ceil(8*0.8)=7) → 上限 7, 但前 6 个 + 1 browser
        self.assertGreaterEqual(len(skel["scenarios"]), 6)
        self.assertLessEqual(len(skel["scenarios"]), 7)

    def test_v_count_20_capped_at_7(self):
        skel = _scenario._build_skeleton_yaml("c3", "scr", v_count=20)
        self.assertLessEqual(len(skel["scenarios"]), 7)

    def test_frontend_design_includes_browser(self):
        skel = _scenario._build_skeleton_yaml("c4", "scr", v_count=4, design_mentions_frontend=True)
        # 至少一个 scenario 含 type=browser 步骤
        any_browser = False
        for sc in skel["scenarios"]:
            for w in sc.get("when", []):
                if w.get("type") == "browser":
                    any_browser = True
                    break
            if any_browser:
                break
        self.assertTrue(any_browser)

    def test_skeleton_includes_covers_field(self):
        """v3.10: skeleton 中每个 Scenario 须含 covers 占位字段."""
        skel = _scenario._build_skeleton_yaml("c5", "scr", v_count=3)
        for sc in skel["scenarios"]:
            self.assertIn("covers", sc)
            # covers 是数组或含占位符 → 都视为'LLM 必填'
            covers_val = sc["covers"]
            self.assertTrue(isinstance(covers_val, list))


if __name__ == "__main__":
    unittest.main()
