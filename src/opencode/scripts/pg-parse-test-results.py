#!/usr/bin/env python3
"""pg-parse-test-results.py - Unified test result processor.

Supports multiple test output formats.

Commands:
  parse --type <playwright|maven> --log-file <path> --out <path>
    Parse test output into structured JSON grouped by test unit.
"""

import hashlib
import json
import os
import re
import sys
from collections import OrderedDict

COMMANDS = ["parse"]


# ==================== Parse: Playwright ====================

def parse_playwright_output(log_path):
    with open(log_path, encoding="utf-8") as f:
        content = f.read()

    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "did_not_run": 0}

    m_total = re.search(r'Running (\d+) tests?', content)
    if m_total:
        summary["total"] = int(m_total.group(1))
    tail_start = int(len(content) * 0.7)
    tail = content[tail_start:]
    m_failed = re.search(r'(\d+)\s+failed', tail)
    if m_failed:
        summary["failed"] = int(m_failed.group(1))
    m_passed = re.search(r'(\d+)\s+passed', tail)
    if m_passed:
        summary["passed"] = int(m_passed.group(1))
    m_skipped = re.search(r'(\d+)\s+skipped', tail)
    if m_skipped:
        summary["skipped"] = int(m_skipped.group(1))
    m_dnr = re.search(r'(\d+)\s+did not run', tail)
    if m_dnr:
        summary["did_not_run"] = int(m_dnr.group(1))

    failure_pattern = re.compile(
        r'^\s+\[([^\]]+)\]\s+›\s+(tests/e2e/[^\s]+?\.spec\.ts):(\d+):(\d+)\s+›\s+(.+)$',
        re.MULTILINE
    )
    failures = []
    for m in failure_pattern.finditer(content):
        failures.append({
            "project": m.group(1),
            "script": m.group(2),
            "line": int(m.group(3)),
            "test_name": m.group(5).strip()
        })

    scripts = OrderedDict()
    for f in failures:
        script = f["script"]
        if script not in scripts:
            scripts[script] = {"target": script, "count": 0, "issues": []}
        scripts[script]["count"] += 1
        scripts[script]["issues"].append({
            "status": "failed",
            "project": f["project"],
            "test": f["test_name"],
            "line": f["line"]
        })

    return {"summary": summary, "failedUnits": list(scripts.values())}


# ==================== Parse: Maven Surefire ====================

def parse_maven_output(log_path):
    with open(log_path, encoding="utf-8") as f:
        content = f.read()

    summary = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

    m_total = re.search(r'Tests run:\s*(\d+)', content)
    if m_total:
        summary["total"] = int(m_total.group(1))

    m_failures = re.search(r'Failures:\s*(\d+)', content)
    if m_failures:
        summary["failed"] = int(m_failures.group(1))

    m_errors = re.search(r'Errors:\s*(\d+)', content)
    if m_errors:
        summary["failed"] += int(m_errors.group(1))

    m_skipped = re.search(r'Skipped:\s*(\d+)', content)
    if m_skipped:
        summary["skipped"] = int(m_skipped.group(1))

    # Extract failure/error lines grouped by test class
    # Format: [ERROR]   ClassName.methodName:line -> ErrorMessage
    # Format: [ERROR]   ClassName.testMethod -> ErrorMessage
    failure_pattern = re.compile(
        r'^\[ERROR\]\s+(?:Failures:\s+)?'
        r'(?:com\.example\.[^\s]+\.)?'
        r'(\w+Test)\.(\w+)'          # ClassName.methodName
        r'(?::(\d+))?\s*'            # optional line
        r'(?:»\s*)?(.+)$',           # error message
        re.MULTILINE
    )

    # Collect failures grouped by class
    class_groups = OrderedDict()
    for m in failure_pattern.finditer(content):
        class_name = m.group(1)
        method_name = m.group(2)
        line = int(m.group(3)) if m.group(3) else 0
        error_msg = m.group(4).strip()

        # Only include full class name for uniqueness
        # Find the full class name by looking at context
        full_class = None
        for line in content.split('\n'):
            if class_name in line and 'test' in line.lower():
                cls_match = re.search(r'(com\.example\.[^\s]+' + re.escape(class_name) + r')', line)
                if cls_match:
                    full_class = cls_match.group(1)
                    break

        target = full_class if full_class else class_name
        if target not in class_groups:
            class_groups[target] = {"target": target, "count": 0, "issues": []}
        class_groups[target]["count"] += 1
        class_groups[target]["issues"].append({
            "status": "failed",
            "test": f"{class_name}.{method_name}",
            "line": line,
            "error": error_msg
        })

    return {"summary": summary, "failedUnits": list(class_groups.values())}


# ==================== Parse: Dispatcher ====================

def parse_test_output(log_path, output_type):
    if output_type == "playwright":
        return parse_playwright_output(log_path)
    elif output_type == "maven":
        return parse_maven_output(log_path)
    else:
        raise ValueError(f"Unknown output type: {output_type}")


def _parse_field(pattern, text, flags=re.MULTILINE):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: {sys.argv[0]} <{'|'.join(COMMANDS)}> [args...]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    if command == "parse":
        log_path = None
        out_path = None
        output_type = "playwright"
        for i, a in enumerate(args):
            if a == "--type" and i + 1 < len(args):
                output_type = args[i + 1]
            if a == "--log-file" and i + 1 < len(args):
                log_path = args[i + 1]
            if a == "--out" and i + 1 < len(args):
                out_path = args[i + 1]
        if not log_path:
            print("--log-file required", file=sys.stderr)
            sys.exit(1)
        result = parse_test_output(log_path, output_type)
        output = json.dumps(result, indent=2, ensure_ascii=False)
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(output)
        print(output)


if __name__ == "__main__":
    main()