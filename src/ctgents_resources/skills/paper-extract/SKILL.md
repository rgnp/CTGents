---
name: paper-extract
description: >
  一篇论文的全面资产拆解。从论证审计（评）到技术组件提取（取）到决策判断（用），
  17 维三阶段流水线。仅在 active paper-deep-read Psyche 判断需要全面榨干一篇论文时使用。
  支持 lite（轻量筛选）和 full（全面拆解）两种深度。
---

# 论文全面榨干

## 前置条件

- `paper-deep-read` Psyche 必须已激活；未激活时拒绝执行。
- 必须已拿到论文正文。摘要只能支持扫描级结论。
- 如果 paper-deep-read card 不存在或过旧，先走 paper-deep-read Skill 完成论证审计。
- 本 Skill 不加载任何 Psyche，不根据关键词自行触发。

## 轴选择

- `depth`：默认 `lite`。lite 跑完后 agent 必须显式判断是否够格切 full。
  够格标准：①在用户边界内 ②有可提取积木 ③有可挖掘矛盾——三选一即可。
  不够格的论文停在 lite，记录原因。
- `domain`：根据论文内容选择。`autonomous-driving` 加载 AD 特殊约束（边界检查/算力/NVSIM v2），
  `general-ml` 使用标准 ML 评估准则。

## 核心原则：判断框架，不是步骤清单

本 Skill 不写「Step 1: D1, Step 2: D2, ...」。写的是三层流水线各自要回答的**判断问题**。
agent 对着问题想答案——答案自然映射到某个维度。

三层流水线：
1. **评（审计层）**：这篇成色怎么样？
2. **取（提取层）**：能拆出什么可复用资产？
3. **用（决策层）**：用户拿这篇怎么办？

详见 `static/core/workflow.md`（深度轴注入 `static/fragments/depth/{lite|full}.md`）。

## 执行约束

- 积木提取必须以原文为唯一事实来源。不凭记忆推断技术细节。
- 矛盾定位必须引用已知论文的具体内容（deep-read card 或原文），不编造对立面。
- 用户决策（D12）只做口头报告，不落文件——决策权在用户。
- 产出分散到正确位置（见 workflow.md 的产出分散规则表），不新建单一「拆解报告」文件。
- 写前检查：积木不重复已有 blocks/、矛盾不重复已有 contradictions-*.md、范式不重复已有 experiment-patterns.md。

## 与 paper-deep-read 的关系

paper-deep-read 覆盖 D1（论证审计）+ 部分 D4/D9。paper-extract 调用其结果作为审计层输入，
不重复审计。如果 deep-read card 不存在或过期，先触发 paper-deep-read。

## 收尾

- depth=lite：输出 lite 结论 + 判断门（够格切 full 吗？）
- depth=full：输出全部三阶段结论 + 更新 reading-tracker + 口头报告 D12
- 完成后询问用户是否需要多篇交叉跑 contradiction-miner
