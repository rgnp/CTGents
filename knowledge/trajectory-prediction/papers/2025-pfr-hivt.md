# PFR-HiVT: Enhancing Multi-Agent Trajectory Prediction with Progressive Feature Refinement

| 字段 | 内容 |
|---|---|
| **会议/期刊** | MDPI Symmetry 2025 |
| **作者** | Bai, Lu 等 |
| **代码** | 未开源（推测） |
| **任务** | 多智能体轨迹预测 |
| **数据集** | Argoverse 1.1 |
| **基于** | HiVT (CVPR 2022) |

---

## 核心贡献（一句话）

在 HiVT 的 global encoder 中引入 **Progressive Feature Refinement（渐进式特征细化）** 模块，用对称性设计增强交互特征提取。

---

## 方法要点

PFR-HiVT 在 HiVT 的基础上做了以下修改：

1. **替换 Global Interactor** — 将 HiVT 原始的全局交互模块替换为 PFR（Progressive Feature Refinement）模块
2. **渐进式聚合** — 多阶段逐步细化 agent 间的交互特征，而非一次性完成
3. **对称性增强** — 利用交通场景中的对称性（左/右、前/后）指导特征学习

**参数量对比**：
- HiVT-64: 0.69M
- PFR-HiVT: 更轻量（具体数值待确认）

---

## 实验结果

| 模型 | minADE@6 | minFDE@6 |
|---|---|---|
| HiVT-64 | 0.80 | 1.17 |
| HiVT-128 | 0.74 | 1.11 |
| PFR-HiVT | **0.703** | **1.078** |

在更少参数的前提下精度提升显著。

---

## 与项目关系

| 维度 | 关系 |
|---|---|
| **直接上游** | 基于 HiVT 的改进工作 |
| **竞争关系** | PFR-HiVT 证明了 HiVT 架构仍有改进空间，尤其是交互模块 |
| **辅助任务** | 没有辅助任务，纯架构改进。与辅助学习目标正交，可以叠加 |
| **风险提示** | MDPI Symmetry 期刊认可度一般，PFR 本身不是可论文化的核心贡献 |

---

## 待办事项

- [ ] 确认 PFR 模块的具体设计细节
- [ ] 评估能否复现其结果作为对比基线
