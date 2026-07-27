#!/usr/bin/env bash
#
# .opencode/skills/pg-propose/scripts/check-env-capability.sh
#
# Checks whether the env-capability cache (.pg/context/env-fingerprint.yaml
# + .pg/context/env-capability.yaml) is still valid by comparing stored
# fingerprints against current .pg/hooks/** state.
#
# Called by pg-propose (1d.5) to decide whether to invoke LLM extraction
# or use the cached version.
#
# Usage: bash check-env-capability.sh          (run from project root)
#
# Exit code: always 0
#
# Output (stdout):
#   HIT:  STATUS=HIT\n---\n<env-capability.yaml content>
#   MISS: STATUS=MISS\nREASON=<reason>\n[DETAIL=<detail>]\n---\nCURRENT_FINGERPRINTS:\n<entries>
#
# Output protocol: STATUS=HIT/MISS line + `---` separator + YAML body
# (LLM agents read the YAML body after the `---` line).
#
# v0.9.0 — STATUS=HIT/MISS + `---` 分隔符协议 (LLM agent 直接读取 `---` 后的 YAML 内容)。

set -uo pipefail

FP_FILE=".pg/context/env-fingerprint.yaml"
CAP_FILE=".pg/context/env-capability.yaml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 路径: .opencode/skills/pg-propose/scripts/ 上溯 4 段到项目根, 再 .pg/skills/src/opencode/scripts/
# 用 cd + pwd 解析避免 bash 对 . 开头路径的 glob 解析差异
PY_FINGERPRINT="$(cd "$SCRIPT_DIR/../../../../.pg/skills/src/opencode/scripts" && pwd)/pg-gen-env-fingerprint.py"

# === Step 1: Compute current fingerprint (writing to a temp file) ===
TMP_FP="$(mktemp -t env-fp.XXXXXX.yaml)"
trap 'rm -f "$TMP_FP"' EXIT

if ! python3 "$PY_FINGERPRINT" --output "$TMP_FP" 2>/dev/null; then
    echo "STATUS=MISS"
    echo "REASON=fingerprint-script-failed"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    cat "$TMP_FP" 2>/dev/null || echo "<compute failed>"
    exit 0
fi

current_project_sha=$(python3 -c "
import sys, yaml
try:
    d = yaml.safe_load(open('$TMP_FP'))
    print(d.get('project_yaml_sha256') or 'NONE')
except Exception:
    print('NONE')
" 2>/dev/null || echo "NONE")

current_files_count=$(python3 -c "
import sys, yaml
try:
    d = yaml.safe_load(open('$TMP_FP'))
    print(len(d.get('files') or {}))
except Exception:
    print(0)
" 2>/dev/null || echo 0)

# === Step 2: Check if capability file exists ===
if [ ! -f "$CAP_FILE" ]; then
    echo "STATUS=MISS"
    echo "REASON=capability-not-found"
    echo "DETAIL=create by running 1d.5 LLM extraction"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "project_yaml_sha256=$current_project_sha"
    echo "files_count=$current_files_count"
    exit 0
fi

# === Step 3: Parse stored fingerprint from env-fingerprint.yaml ===
stored_project_sha=$(python3 -c "
import yaml
try:
    d = yaml.safe_load(open('$FP_FILE'))
    print(d.get('project_yaml_sha256') or 'NONE')
except FileNotFoundError:
    print('MISSING')
except Exception:
    print('NONE')
" 2>/dev/null || echo "MISSING")

stored_files_count=$(python3 -c "
import yaml
try:
    d = yaml.safe_load(open('$FP_FILE'))
    print(len(d.get('files') or {}))
except FileNotFoundError:
    print(0)
except Exception:
    print(0)
" 2>/dev/null || echo 0)

# === Step 4: Compare ===
if [ "$stored_project_sha" = "MISSING" ]; then
    echo "STATUS=MISS"
    echo "REASON=fingerprint-file-missing"
    echo "DETAIL=.pg/context/env-fingerprint.yaml does not exist; run pg-gen-env-fingerprint.py"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "project_yaml_sha256=$current_project_sha"
    echo "files_count=$current_files_count"
    exit 0
fi

if [ "$current_project_sha" != "$stored_project_sha" ]; then
    echo "STATUS=MISS"
    echo "REASON=project-yaml-changed"
    echo "DETAIL=stored=$stored_project_sha current=$current_project_sha"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "project_yaml_sha256=$current_project_sha"
    echo "files_count=$current_files_count"
    exit 0
fi

# Compare per-file hashes
hash_mismatch=$(python3 -c "
import yaml
cur = yaml.safe_load(open('$TMP_FP')) or {}
sto = yaml.safe_load(open('$FP_FILE')) or {}
cur_files = cur.get('files') or {}
sto_files = sto.get('files') or {}
added   = sorted(set(cur_files) - set(sto_files))
removed = sorted(set(sto_files) - set(cur_files))
changed = sorted([k for k in cur_files if k in sto_files and cur_files[k] != sto_files[k]])
if added:   print('ADDED:' + ','.join(added[:5]))
if removed: print('REMOVED:' + ','.join(removed[:5]))
if changed: print('CHANGED:' + ','.join(changed[:5]))
if not (added or removed or changed): print('OK')
" 2>/dev/null || echo "COMPARE_FAILED")

if [ "$hash_mismatch" != "OK" ]; then
    reason_code=$(echo "$hash_mismatch" | cut -d: -f1)
    detail=$(echo "$hash_mismatch" | cut -d: -f2-)
    echo "STATUS=MISS"
    echo "REASON=$reason_code"
    echo "DETAIL=$detail"
    echo "---"
    echo "CURRENT_FINGERPRINTS:"
    echo "project_yaml_sha256=$current_project_sha"
    echo "files_count=$current_files_count"
    exit 0
fi

# === Step 5: HIT — output full YAML content ===
echo "STATUS=HIT"
echo "---"
cat "$CAP_FILE"
