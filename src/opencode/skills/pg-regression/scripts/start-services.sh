#!/usr/bin/env bash
# start-services.sh — start env's roles' instances via pg-run-hook.py
#
# usage:
#   bash start-services.sh <env-name> <role1> [<role2> ...]
#
# behavior:
#   1. read .pg/project.yaml
#   2. for each role, each instance under that role
#   3. delegate each actions.start to pg-run-hook.py
#   4. serial execution; any failure -> exit N (no retry)

set -euo pipefail

ENV_NAME="${1:?usage: start-services.sh <env-name> <role1> [role2...]}"
shift
ROLES=("$@")

if [ ${#ROLES[@]} -eq 0 ]; then
    echo "[start-services] no roles specified, skipping"
    exit 0
fi

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CONFIG="$PROJECT_ROOT/.pg/project.yaml"
RUNNER="$PROJECT_ROOT/.pg/skills/src/runtime/lib/pg-run-hook.py"

if [ ! -f "$CONFIG" ]; then
    echo "[start-services] config not found: $CONFIG" >&2
    exit 2
fi

if [ ! -f "$RUNNER" ]; then
    echo "[start-services] pg-run-hook.py not found: $RUNNER" >&2
    exit 2
fi

# Parse config.yaml: extract instances with their actions.start commands
read -r -d '' PY_PARSE <<'PYEOF' || true
import sys, yaml, json
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)
env = cfg.get("environments", {}).get(sys.argv[2])
if env is None:
    print(json.dumps({"error": f"environment {sys.argv[2]} not found"}))
    sys.exit(0)
out = []
for role in sys.argv[3:]:
    role_cfg = env.get("roles", {}).get(role)
    if role_cfg is None:
        continue
    for inst in role_cfg.get("instances", []):
        act = role_cfg.get("actions", {}).get("start")
        if not act:
            continue
        args = [
            str(a)
              .replace("{role}", role)
              .replace("{instance.name}", inst.get("name", ""))
              .replace("{instance.host}", inst.get("host", ""))
              .replace("{lines:100}", "100")
            for a in act.get("args", [])
        ]
        cmd = "bash " + act["script"] + (" " + " ".join(args) if args else "")
        out.append({
            "role": role,
            "instance": inst.get("name", ""),
            "instance_host": inst.get("host", ""),
            "cmd": cmd,
            "timeout": act.get("timeout_seconds", 60),
        })
print(json.dumps({"instances": out}))
PYEOF

RESULT=$(python3 -c "$PY_PARSE" "$CONFIG" "$ENV_NAME" "${ROLES[@]}")

if echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('error') else 1)" 2>/dev/null; then
    ERR=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['error'])")
    echo "[start-services] $ERR" >&2
    exit 2
fi

# Delegate each instance action to pg-run-hook.py
INSTANCES=$(echo "$RESULT" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['instances']))")
COUNT=$(echo "$INSTANCES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

if [ "$COUNT" -eq 0 ]; then
    echo "[start-services] no instances to start for env=$ENV_NAME roles=${ROLES[*]}"
    exit 0
fi

FAILED=0
for idx in $(seq 0 $((COUNT - 1))); do
    SPEC=$(echo "$INSTANCES" | python3 -c "
import json, sys
inst = json.load(sys.stdin)[$idx]
print(json.dumps({
    'cmd': inst['cmd'],
    'env': '$ENV_NAME',
    'role': inst['role'],
    'instance_name': inst['instance'],
    'instance_host': inst['instance_host'],
    'timeout_seconds': inst['timeout'],
}))
")
    ROLE=$(echo "$SPEC" | python3 -c "import json,sys; print(json.load(sys.stdin)['role'])")
    INST=$(echo "$SPEC" | python3 -c "import json,sys; print(json.load(sys.stdin)['instance_name'])")
    TIMEOUT=$(echo "$SPEC" | python3 -c "import json,sys; print(json.load(sys.stdin).get('timeout_seconds', 60))")

    echo "[start-services] [$((idx + 1))/$COUNT] starting $ROLE/$INST (timeout=${TIMEOUT}s)"
    if ! echo "$SPEC" | python3 "$RUNNER"; then
        echo "[start-services] FAILED $ROLE/$INST" >&2
        FAILED=$((FAILED + 1))
        exit 1
    fi
done

if [ "$FAILED" -eq 0 ]; then
    echo "[start-services] all $COUNT instances started"
fi
exit "$FAILED"
