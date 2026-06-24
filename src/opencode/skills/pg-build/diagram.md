# pg-build 流程图

```mermaid
flowchart TB
    classDef stage fill:#e8f4f8,stroke:#2980b9,stroke-width:2px
    classDef dispatch fill:#d5f5e3,stroke:#27ae60,stroke-width:1px
    classDef deploy fill:#fef9e7,stroke:#f39c12,stroke-width:1px
    classDef gate fill:#fdedec,stroke:#e74c3c,stroke-width:1px
    classDef loop fill:#f4ecf7,stroke:#8e44ad,stroke-width:1px
    classDef done fill:#d5f5e3,stroke:#1e8449,stroke-width:3px
    classDef fail fill:#fadbd8,stroke:#c0392b,stroke-width:3px

    START([Start]) --> INIT[Feature Branch + Init Commit]
    INIT --> DETECT{any active change?}
    DETECT -->|no| EXIT([Exit])
    DETECT -->|yes| NEXT($RUNNER next)

    NEXT --> STAGE1_B

    subgraph STAGE1[Stage 1: dev-isolated - 顺序执行]
        STAGE1_H[environment.required: false]:::stage

        STAGE1_B[Track: backend]:::dispatch --> S1_B_TD[test -> dev]:::dispatch
        S1_B_TD --> S1_B_V{verify}:::dispatch
        S1_B_V -->|PROCEED| S1_B_G[gate]:::gate
        S1_B_V -->|ESCALATE| S1_B_F[fix]:::dispatch
        S1_B_F --> S1_B_TD
        S1_B_G -->|PASS| STAGE1_A
        S1_B_G -->|FAIL| S1_B_GF[gate-fix]:::loop
        S1_B_GF --> S1_B_TD
        S1_B_F -.->|over max_fix_retries| S1_B_G

        STAGE1_A[Track: agent]:::dispatch --> S1_A_TD[test -> dev]:::dispatch
        S1_A_TD --> S1_A_V{verify}:::dispatch
        S1_A_V -->|PROCEED| S1_A_G[gate]:::gate
        S1_A_V -->|ESCALATE| S1_A_F[fix]:::dispatch
        S1_A_F --> S1_A_TD
        S1_A_G -->|PASS| STAGE1_F
        S1_A_G -->|FAIL| S1_A_GF[gate-fix]:::loop
        S1_A_GF --> S1_A_TD
        S1_A_F -.->|over max_fix_retries| S1_A_G

        STAGE1_F[Track: frontend]:::dispatch --> S1_F_TD[test -> dev]:::dispatch
        S1_F_TD --> S1_F_V{verify}:::dispatch
        S1_F_V -->|PROCEED| S1_F_G[gate]:::gate
        S1_F_V -->|ESCALATE| S1_F_F[fix]:::dispatch
        S1_F_F --> S1_F_TD
        S1_F_G -->|PASS| S1_ALL{all PASS?}
        S1_F_G -->|FAIL| S1_F_GF[gate-fix]:::loop
        S1_F_GF --> S1_F_TD
        S1_F_F -.->|over max_fix_retries| S1_F_G

        S1_ALL -->|yes| S1_NEXT[to Stage 2]
    end

    S1_NEXT --> STAGE2

    subgraph STAGE2[Stage 2: dev-mock-integration - 顺序执行]
        STAGE2_H[environment.required: true]:::stage

        S2_D[deploy backend role<br/>backend.start + health]:::deploy

        S2_B[Track: backend]:::dispatch --> S2_B_TD[test -> dev<br/>+ mock test]:::dispatch
        S2_B_TD --> S2_B_V{verify}:::dispatch
        S2_B_V -->|PROCEED| S2_B_G[gate]:::gate
        S2_B_V -->|ESCALATE| S2_B_F[fix]:::dispatch
        S2_B_F --> S2_B_TD
        S2_B_G -->|PASS| S2_D2
        S2_B_G -->|FAIL| S2_B_GF[gate-fix]:::loop
        S2_B_GF --> S2_B_TD
        S2_B_F -.->|over max_fix_retries| S2_B_G

        S2_D2[deploy agent role<br/>agent.start + health]:::deploy

        S2_A[Track: agent]:::dispatch --> S2_A_TD[test -> dev<br/>+ mock test]:::dispatch
        S2_A_TD --> S2_A_V{verify}:::dispatch
        S2_A_V -->|PROCEED| S2_A_G[gate]:::gate
        S2_A_V -->|ESCALATE| S2_A_F[fix]:::dispatch
        S2_A_F --> S2_A_TD
        S2_A_G -->|PASS| S2_D3
        S2_A_G -->|FAIL| S2_A_GF[gate-fix]:::loop
        S2_A_GF --> S2_A_TD
        S2_A_F -.->|over max_fix_retries| S2_A_G

        S2_D3[deploy frontend role<br/>frontend.start + health]:::deploy

        S2_F[Track: frontend]:::dispatch --> S2_F_TD[test -> dev<br/>+ mock test]:::dispatch
        S2_F_TD --> S2_F_V{verify}:::dispatch
        S2_F_V -->|PROCEED| S2_F_G[gate]:::gate
        S2_F_V -->|ESCALATE| S2_F_F[fix]:::dispatch
        S2_F_F --> S2_F_TD
        S2_F_G -->|PASS| S2_ALL{all PASS?}
        S2_F_G -->|FAIL| S2_F_GF[gate-fix]:::loop
        S2_F_GF --> S2_F_TD
        S2_F_F -.->|over max_fix_retries| S2_F_G

        S2_ALL -->|yes| S2_STOP[stop roles]:::deploy
    end

    S2_STOP --> S3_NEXT[to Stage 3]

    subgraph STAGE3[Stage 3: real-integration]
        STAGE3_H[environment.required: true<br/>environment: dev-3tier]:::stage

        S3_D[deploy all roles<br/>backend.start frontend.start agent.start]:::deploy

        S3_V{verify<br/>E2E test}:::dispatch

        S3_D --> S3_V
        S3_V -->|PROCEED| S3_G[gate]:::gate
        S3_V -->|ESCALATE| S3_F[fix]:::dispatch
        S3_F --> S3_V
        S3_G -->|PASS| S3_OK(( ))
        S3_G -->|FAIL| S3_GF[gate-fix]:::loop
        S3_GF --> S3_V
        S3_F -.->|over max_fix_retries| S3_G
    end

    S3_OK --> S3_STOP[stop all roles + cleanup]:::deploy

    S3_STOP --> FG[final-gate<br/>cross-track review]:::gate
    FG -->|PASS| ARC[auto archive<br/>changes -> archive/]:::done
    FG -->|FAIL| KI[known-issues.md<br/>manual intervention]:::fail
    ARC --> DONE([DONE]):::done
    KI --> WFF([workflow_failed]):::fail

    subgraph LEGEND[图例]
        L1[stage box]:::stage
        L2[agent dispatch]:::dispatch
        L3[deploy action]:::deploy
        L4[gate review]:::gate
        L5[fix loop]:::loop
        L6[success]:::done
        L7[failure]:::fail
    end
```

## 说明

### 三种 stage 的区别

| 维度 | dev-isolated | dev-mock-integration | real-integration |
|------|-------------|---------------------|-----------------|
| deployment | 不启动 | 每个 track 前启自己 role | 启全部 role |
| 测试范围 | unit test | mock_integration test | real_integration test |
| 执行方式 | 顺序：backend → agent → frontend | 顺序：backend → agent → frontend | 单个 track |
| fix_routing | source（本 track 内修） | source | auto（按 diff 跳到对应 track） |

### 执行顺序

所有 stage 内的 track **顺序执行**（非并发）：

```
Stage 1  → backend TDV → agent TDV → frontend TDV → gate all_pass → Stage 2
Stage 2  → deploy backend → backend TDV → deploy agent → agent TDV → deploy frontend → frontend TDV → stop → Stage 3
Stage 3  → deploy all → verify E2E → gate → stop → final-gate
```

### TDV 循环（每 track）

```
test → dev → verify → 通过 → gate → PASS → 下一 track
                   └→ 失败 → fix → re-verify → ... (max_fix_retries)
                                                └→ 超限 → gate (强制)
```

### 环境启动

仅 `environment.required=true` 的 stage 会调 `role.actions.start`，结束后调 `role.actions.stop`。