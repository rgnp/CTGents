---
name: aux-coordinate-system-alignment
description: 辅助任务设计中，aux target 必须和主任务使用同一坐
metadata:
  type: strategy
  updated: 2026-06-03T09:22:50Z
---

辅助任务设计中，aux target 必须和主任务使用同一坐标系。HiVT 使用 agent-centric 坐标变换，aux anchor target 也必须用相对坐标（future[t] - current_position），而不是全局绝对坐标。否则坐标系不匹配会导致辅助任务失败。很多辅助任务失败不是因为 idea 不行，而是 target 坐标系和模型表征不匹配。【来自PATA v2评审】
