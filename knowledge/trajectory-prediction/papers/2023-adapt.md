# ADAPT: Efficient Multi-Agent Trajectory Prediction with Adaptation

| 字段 | 内容 |
|---|---|
| **会议** | ICCV 2023 |
| **作者** | Görkay Aydemir, Adil Kaan Akan, Fatma Güney |
| **代码** | [GitHub](https://github.com/gorkaydemir/ADAPT) |
| **任务** | 多智能体轨迹预测 |
| **数据集** | Argoverse, Interaction |

---

## 核心贡献（一句话）

提出动态权重学习 + 自适应预测头，在 HiVT 的设计哲学基础上进一步提升效率，以更少计算量超越 SOTA。

---

## 方法要点

1. **自适应头** — 每个 agent 的动态权重，在不增加模型大小前提下增强容量
2. **终点条件预测** — 结合梯度停止策略，优化多模态轨迹生成
3. **联合预测** — 同时预测场景中所有 agent 的轨迹

与 HiVT 的关系：继承了分层编码 + 高效推理的设计理念，但在聚合方式和预测头上有显著不同。

---

## 实验结果

| 数据集 | 指标 | ADAPT vs HiVT-128 |
|---|---|---|
| Argoverse (single) | minADE | **0.71 vs 0.74** |
| Argoverse (single) | minFDE | **1.08 vs 1.11** |
| Argoverse (multi) | minADE | 优于 HiVT |
| Interaction | minADE | 优于 HiVT |

计算开销显著低于 HiVT。

---

## 与项目关系

| 维度 | 关系 |
|---|---|
| **方法继承** | 与 HiVT 属于同一技术族 |
| **辅助任务** | 无辅助任务，纯架构+训练策略改进 |
| **对比价值** | 可作为 HiVT 改进方法的上限参考 |

---

## 待办事项

- [ ] 阅读 ADAPT 论文全文，重点理解 adaptive head 的设计
