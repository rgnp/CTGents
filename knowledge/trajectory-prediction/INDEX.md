# 轨迹预测辅助学习目标 — 知识库索引

> 本知识库用于积累轨迹预测中「通过额外学习目标增强已有模型」方向的论文、方法和实验结论。
> 维护方式：找到新论文 → 录入论文卡片 → 更新 topics → 更新 gaps
> RAG 索引后可通过自然语言检索。

---

## 子方向速览

| 子方向 | 核心问题 | 代表论文 | 状态 |
|---|---|---|---|
| [置信度校准](topics/calibration.md) | 模型不知道自己的预测有多可靠 | CCTR (AAAI 2024) | 有工作但未饱和 |
| [交互建模](topics/interaction.md) | 模型不感知agent之间的相互影响 | SSL-Interactions (arxiv 2024) | 有工作，反事实方向缺 |
| [物理可行性](topics/physical-feasibility.md) | 预测轨迹违反运动学约束 | TPK (arxiv 2025) | 新方向，有空间 |
| [规划兼容性](topics/planning-compatibility.md) | 预测指标好但规划器用不了 | 暂无专门工作 | **空白** |
| [自监督预训练](topics/self-supervised.md) | 如何从无标签数据中学习表示 | Plan-MAE (arxiv 2025) | 正在兴起 |
| [表示学习](topics/representation-learning.md) | 隐空间缺乏语义结构 | 无专门工作 | **空白** |

## 论文总表

| 论文 | 会议/年份 | 辅助任务类型 | 缺失能力 C | 开源 | 录入状态 |
|---|---|---|---|---|---|
| SSL-Interactions | arxiv 2024.01 | 预测式 | 交互理解 | ✅ | ✅ 已录入 |
| CCTR | AAAI 2024 | 规正式（后处理） | 置信度校准 | ❌ | ✅ 已录入 |
| UniTraj | ECCV 2024 | 框架（非辅助任务） | 跨域泛化 | ✅ | ✅ 已录入 |
| Plan-MAE | arxiv 2025.07 | 重构式 | 场景理解 | ❓ | ✅ 已录入 |
| TPK | arxiv 2025.05 | 可微物理损失 | 运动学可行性 | ❓ | ✅ 已录入 |
| Progressive Pretext | ECCV 2024 | 预测式 | 行人交互 | ❓ | 📝 待读全文 |
| PreCLN | Neurocomputing 2022 | 对比式 | 表示结构 | ❓ | 📝 待读 |
| Contrastive MAE | RAS 2025 | 重构+对比 | 表示结构 | ❓ | 📝 待读 |

## 已知失败方向 / 被验证无效的尝试

详见 [meta/dead-ends.md](meta/dead-ends.md)

## 经过交叉验证后的空白

详见 [meta/gaps.md](meta/gaps.md)
