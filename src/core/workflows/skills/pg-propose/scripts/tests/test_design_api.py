#!/usr/bin/env python3
"""v3.x: pg-validate-proposal.py design-api 子命令单测。

覆盖：
- 正常 design.md（每个 endpoint 有 Request/Response Body）→ PASS
- 缺 Response Body → FAIL
- 缺 Request Body → FAIL
- 缺 JSON 代码块 → FAIL
- 主端点含子段标题（"### POST - Request Body" 等）→ 不重复报错
- 旧格式（`### 创建 Project` + body 内 `**POST /api/...**`）→ 正确识别
- 反引号格式（`**`POST /api/...`**`）→ 正确识别
- 无 API 设计章节 → skip
- 子段标题（"- Request Body"/"- Response Body (200)"）→ 跳过独立校验
- archive fallback（archive/<date>-<change>/design.md）→ 正确解析
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_SCRIPT_DIR)


def _run(script_args, cwd=None, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, os.path.join(_SCRIPTS_DIR, "pg-validate-proposal.py")]
        + script_args,
        capture_output=True, text=True, env=env, cwd=cwd,
    )


class TestDesignApiValidation(unittest.TestCase):
    """pg-validate-proposal.py design-api 子命令。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg-test-design-api-")
        self.project_yaml = os.path.join(self.tmp, ".pg", "project.yaml")
        os.makedirs(os.path.dirname(self.project_yaml), exist_ok=True)
        with open(self.project_yaml, "w") as f:
            f.write("schema: spec-driven\n")
        self.changes_dir = os.path.join(self.tmp, ".pg", "changes")
        os.makedirs(self.changes_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_design(self, change, content, archive=False):
        if archive:
            change_dir = os.path.join(
                self.changes_dir, "archive", f"2026-07-18-{change}"
            )
        else:
            change_dir = os.path.join(self.changes_dir, change)
        os.makedirs(change_dir, exist_ok=True)
        with open(os.path.join(change_dir, "design.md"), "w") as f:
            f.write(content)

    def _run_validate(self, change):
        return _run(
            ["design-api", change],
            cwd=self.tmp,
            env_extra={"PG_PROJECT_ROOT": self.tmp},
        )

    # ---------- 正例 ----------

    def test_full_coverage_pass(self):
        """完整 design：每个 endpoint 都有 Request/Response Body + JSON 示例。"""
        self._write_design("good", """# good 设计

## API 设计

### GET /api/foo/v1/items

### GET /api/foo/v1/items - Request Body
```json
{}
```

### GET /api/foo/v1/items - Response Body (200)
```json
{"code": 0, "data": []}
```
""")
        result = self._run_validate("good")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("OK", result.stdout)
        self.assertIn("HTTP 端点总数: 1", result.stdout)

    def test_old_format_with_bold_method_pass(self):
        """旧格式：### 创建 Project + body 内 `**POST /api/...**` → 正确识别。"""
        self._write_design("oldformat", """# old 设计

## API 设计

### 创建 Project（扩展现有接口）

**POST /api/project/v1/tenants/{tenantId}/projects**

### 创建 Project（扩展现有接口） - Request Body
```json
{"name": "x"}
```

### 创建 Project（扩展现有接口） - Response Body (201)
```json
{"code": 0, "data": {"id": "abc"}}
```
""")
        result = self._run_validate("oldformat")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("HTTP 端点总数: 1", result.stdout)

    def test_backtick_format_pass(self):
        """反引号格式：`**`POST /api/...`**`（含反引号 inline code）→ 正确识别。"""
        self._write_design("backtick", """# 设计

## API 设计

### 创建 Foo

**`POST /api/foo/v1/items`**

### 创建 Foo - Request Body
```json
{"name": "x"}
```

### 创建 Foo - Response Body (201)
```json
{"code": 0}
```
""")
        result = self._run_validate("backtick")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")

    def test_no_api_section_skip(self):
        """design.md 无 ## API 设计 章节 → skip，exit 0。"""
        self._write_design("nosection", """# 设计

## 数据模型

无 API

## 组件设计

无 API
""")
        result = self._run_validate("nosection")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("跳过 API Contract 校验", result.stdout)

    def test_archive_fallback(self):
        """archive/<date>-<change>/design.md → 正确解析（archive fallback）。"""
        self._write_design("archived", """# 设计

## API 设计

### GET /api/x

### GET /api/x - Request Body
```json
{}
```

### GET /api/x - Response Body (200)
```json
{"code": 0}
```
""", archive=True)
        result = self._run_validate("archived")
        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        self.assertIn("HTTP 端点总数: 1", result.stdout)

    # ---------- 反例 ----------

    def test_missing_response_body_fail(self):
        """endpoint 缺 Response Body → FAIL，exit 1。"""
        self._write_design("noresp", """# 设计

## API 设计

### GET /api/x

### GET /api/x - Request Body
```json
{}
```
""")
        result = self._run_validate("noresp")
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")
        self.assertIn("缺 Response Body", result.stderr)
        self.assertIn("GET /api/x", result.stderr)

    def test_missing_request_body_fail(self):
        """endpoint 缺 Request Body → FAIL，exit 1。"""
        self._write_design("noreq", """# 设计

## API 设计

### POST /api/x

### POST /api/x - Response Body (201)
```json
{"code": 0}
```
""")
        result = self._run_validate("noreq")
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")
        self.assertIn("缺 Request Body", result.stderr)

    def test_missing_json_example_fail(self):
        """endpoint body 完全无 JSON 代码块 → FAIL。"""
        self._write_design("nojson", """# 设计

## API 设计

### GET /api/x

### GET /api/x - Request Body
无示例，仅文字描述

### GET /api/x - Response Body (200)
也仅文字描述
""")
        result = self._run_validate("nojson")
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")
        self.assertIn("缺 JSON 代码块", result.stderr)

    def test_subsegment_title_not_double_counted(self):
        """子段标题（如 '- Response Body (200)'）不重复作为独立 endpoint 校验。

        父端点有 Request Body + Response Body（子段形式），不应报缺 Request/Response。
        """
        self._write_design("subsegment", """# 设计

## API 设计

### DELETE /api/foo/{id}

### DELETE /api/foo/{id} - Response Body (204)
```json
{"code": 0}
```

### DELETE /api/foo/{id} - Response Body (404)
```json
{"code": 404}
```
""")
        result = self._run_validate("subsegment")
        # 父端点无 Request Body → FAIL
        # 但子段标题本身不重复报错
        self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")
        # 只应报错父端点 1 次（Request 缺，Response 子段已提供）
        self.assertEqual(result.stderr.count("缺 Request Body"), 1)
        # Response 有 2 个子段满足，不应报错
        self.assertEqual(result.stderr.count("缺 Response Body"), 0)


if __name__ == "__main__":
    unittest.main()