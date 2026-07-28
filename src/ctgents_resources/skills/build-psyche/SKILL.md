---
name: build-psyche
description: 新建、蒸馏、重构或评估项目 Psyche。仅在 active psyche-building Psyche 判断需要进行知识工程、职责拆分、Manifest 更新或行为验证时使用；不用于普通提示词润色。
---

# 构建 Psyche

## 前置条件

- `psyche-building` Psyche 必须 active。
- 不根据领域关键词自动创建 Psyche；先证明存在独立判断增量。
- 本 Skill 不自行加载 Psyche，也不把工作流或时变事实留在 core。

## 工作流

1. **定义缺口**：写出不加载时会遗漏的判断，以及 4–8 个 Competency Questions。
2. **核对边界**：检查现有 Psyche 能否覆盖；明确父依赖、可能冲突和不适用范围。
3. **收集证据**：需要外部知识时读取 [证据协议](references/evidence-protocol.md)，把事实材料存入 Knowledge，不直接堆进 core。
4. **蒸馏判断**：提炼 3–7 个稳定不动点、关键矛盾、负面知识和 Knowledge 查询边界。
5. **分层**：可重复步骤写入 Skill；模板和长协议进入 references；时变结论进入 Knowledge。
6. **声明契约**：更新 Manifest 的版本、依赖、scope、judgment delta、Skills、conflicts 和 exit checks。
7. **验证**：读取 [行为评测](references/behavior-evaluation.md)，测试加载前后差异、误触发和边界。

## 操作轴

- `create`：执行完整流程并创建最小可用 Psyche。
- `refine`：先量化体积、重复和层次污染，再保持判断语义地蒸馏。
- `evaluate`：只输出证据化诊断，不修改文件。

## 完成标准

- Core 删除任一剩余不动点都会损失明确判断能力。
- 依赖和冲突可机械校验。
- Skill owner 方向为 Psyche → Skill。
- 行为样例证明目标判断增量存在，同时不相关任务不会被污染。
- Manifest、core、Skill 与测试版本一致。
