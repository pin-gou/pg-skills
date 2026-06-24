---
name: 2.1-pg-propose-refine
description: 2.1 根据 1-propose-review 的决策，对 proposal.md/design.md/tasks.md 做进一步完善
trigger: slash
model: pg-router/pg-master
---

加载 pg-propose-refine skill，对 `.pg/changes/<change-name>/` 下的 proposal.md、design.md、tasks.md 进行系统化评审。
