#!/usr/bin/env bash
# pg-skills template: environment describe_env action (v7).
#
# 用法:
#   1. 把本文件复制到 .pg/hooks/describe-env.sh
#   2. 在 project.yaml 注册:
#        environments.<env>.describe_env:
#          script: .pg/hooks/describe-env.sh
#          timeout_seconds: 60    # optional, 默认 60
#   3. chmod +x
#   4. 按本项目的真实环境实现 body (本模板给 3 套示例: K8s / DB+Cache / 多服务)
#
# 本模板对应 schema 节点:
#   environments.<env>.describe_env.script
#
# 由 pg-invoke-hook.py --action describe_env 调起 (PG_HOOK_TYPE=describe_env),
# 调用方限定 (v7): pg-propose / pg-fix-issue / pg-regression / ad-hoc.
#
# 注入 env vars (SSOT: src/runtime/spec/hook-env-vars.yaml v6+):
#   - PG_RUN_CALLER     调用方身份
#   - PG_PROJECT_ROOT   项目根
#   - PG_SESSION_ID     session-id (per-SKILL 路由标识)
#   - PG_CHANGE_ID      change-id (v7 起与 --session 等价; hook 仅消费作日志标识)
#   - PG_ENV_NAME       目标 environment 名
#   - PG_OUTPUT_PATH    env-description.yaml 输出绝对路径 (必须写入)
#   - PG_HOOK_LOG_DIR   日志目录
#   - PG_LOG_FILE       hook stdout/stderr 目标
#   - PG_HOOK_TIMEOUT   超时秒数
#
# 行为:
#   - 只读探测, 不修改环境状态
#   - 输出 YAML 必须严格符合 src/runtime/spec/env-description.schema.json
#   - 失败: exit 非 0, 调用方中断 (Q2 决策: 中断, 无 fallback)
#   - 失败现场: 写 ${PG_OUTPUT_PATH}.partial 调试信息, 退出前 echo 到 stderr
#
# 重要: prepare_env 与 describe_env 独立. describe_env 不调用 prepare_env,
#       也不假设 prepare_env 已执行. 两脚本作者各自维护.
#       (Q3 决策: 两脚本独立)
#       语义契约: describe_env 的产出 (env-description.yaml) 描述的是
#       prepare_env 成功执行后该环境的预期基线状态. pg-define/pg-propose 的
#       LLM 应理解: pg-build 会先调 prepare_env, 确保成功后才执行 scenario
#       track, 届时环境状态应与 env-description.yaml 一致.

set -uo pipefail  # 注意: 不加 -e, 由 hook-helpers.sh trap ERR 控制

# === 必读检查 ===
: "${PG_RUN_CALLER:?describe_env requires PG_RUN_CALLER}"
: "${PG_PROJECT_ROOT:?describe_env requires PG_PROJECT_ROOT}"
: "${PG_CHANGE_ID:?describe_env requires PG_CHANGE_ID}"
: "${PG_ENV:?describe_env requires PG_ENV}"
: "${PG_OUTPUT_PATH:?describe_env requires PG_OUTPUT_PATH}"

export PG_SKILLS_PATH="${PG_SKILLS_PATH:-$PROJECT_ROOT/.pg/skills}"
source "$PG_SKILLS_PATH/src/runtime/lib/hook-helpers.sh"

# === 失败现场处理 ===
# 失败时写 partial 调试信息 + 退出非 0, 调用方 (pg-invoke-hook.py) 中断.
trap 'pg_fail_on_error $? $LINENO' ERR
on_failure() {
    local exit_code=$?
    local line_no=$1
    echo "[describe-env] FAILED at line $line_no (exit=$exit_code)" >&2
    mkdir -p "$(dirname "${PG_OUTPUT_PATH}")"
    cat > "${PG_OUTPUT_PATH}.partial" <<EOF
schema_version: 1
described_by: describe-env.sh
described_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
described_for:
  caller: ${PG_RUN_CALLER}
  change: ${PG_CHANGE_ID}
  environment: ${PG_ENV_NAME}
described_status: failed
environments: {}
EOF
    exit 1
}
trap 'on_failure $LINENO' ERR

# === 确保输出目录存在 ===
mkdir -p "$(dirname "${PG_OUTPUT_PATH}")"

# === 探测逻辑 (TODO: 按项目替换) ===
# 模板给 3 套场景示例, 用户按需选择一种或合并:

# 场景 1: K8s 集群 + 多版本
#   适用: 需要在多个 K8s 版本上验证 helm chart / CRD 兼容性
#   探测: kubectl get nodes -o name; kubectl version; helm list
#
# 场景 2: DB + Cache
#   适用: 业务依赖关系型 DB + Redis, 需要验证 seed 状态
#   探测: psql / mysql SHOW TABLES; redis-cli DBSIZE
#
# 场景 3: 多服务集成
#   适用: 业务上下游服务, 需要验证 endpoint 可达 + mock 状态
#   探测: curl -fsS http://.../healthz; 检查 mock 服务响应

# === 真实实现示例 (场景 1: K8s + 多版本) ===
# 替换以下 TODO 块为项目真实探测逻辑
{
    echo "describe_env: starting (PG_RUN_CALLER=${PG_RUN_CALLER}, env=${PG_ENV_NAME})"

    # ---- TODO: 替换为真实探测 ----
    # 示例: 探测 K8s 集群
    # K8S_CLUSTERS=$(kubectl get nodes -o name 2>/dev/null || echo "unknown")
    # DB_TABLES=$(mysql -h 10.0.0.5 -u root -e "SHOW TABLES" 2>/dev/null || echo "unknown")
    #
    # ---- 真实示例占位 (用户必须替换) ----
    INFRA_SERVICES_YAML="[]"
    BUSINESS_SYSTEMS_YAML="[]"
    DATA_RESOURCES_YAML="[]"
    CONFIG_RESOURCES_YAML="[]"
    RUNTIME_ENVIRONMENT_YAML="[]"
    EXTERNAL_DEPENDENCIES_YAML="[]"
    RELATIONS_YAML="[]"

    # === 输出 env-description.yaml ===
    cat > "${PG_OUTPUT_PATH}" <<EOF
schema_version: 1
described_by: describe-env.sh
described_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
described_for:
  caller: ${PG_RUN_CALLER}
  change: ${PG_CHANGE_ID}
  environment: ${PG_ENV_NAME}
environments:
  ${PG_ENV_NAME}:
    infra_services: ${INFRA_SERVICES_YAML}
    business_systems: ${BUSINESS_SYSTEMS_YAML}
    data_resources: ${DATA_RESOURCES_YAML}
    config_resources: ${CONFIG_RESOURCES_YAML}
    runtime_environment: ${RUNTIME_ENVIRONMENT_YAML}
    external_dependencies: ${EXTERNAL_DEPENDENCIES_YAML}
    relations: ${RELATIONS_YAML}
EOF

    echo "describe_env: wrote ${PG_OUTPUT_PATH}"
} || {
    # trap ERR 已处理, 此处仅作日志
    echo "[describe-env] probe failed" >&2
    exit 1
}

# === 成功退出 ===
START=$(date +%s)
DURATION=$(($(date +%s) - START))
pg_exit --status=pass --duration=$DURATION \
        --metadata="env=\"${PG_ENV_NAME}\" change=\"${PG_CHANGE_ID}\" output=\"${PG_OUTPUT_PATH}\""