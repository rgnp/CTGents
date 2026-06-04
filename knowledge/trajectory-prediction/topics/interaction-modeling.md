# 交互建模在轨迹预测中的方法谱

> 核心问题：如何让模型理解 agent 之间的相互影响。

## 方法谱系

```
交互建模方法
├── 隐式交互（模型自动学习）
│   ├── SocialGAN Pooling (2018) — max-pooling聚合邻居
│   ├── HiVT local encoder (2022) — 局部注意力
│   ├── SceneTransformer (2021) — 全场景注意力
│   └── Wayformer (2023) — 简化注意力
│
├── 显式交互（结构化建模）
│   ├── LaneGCN (2020) — 车道图消息传递
│   ├── Trajectron++ (2020) — 图结构RNN
│   ├── M2I (2022) — influencer/reactor分解
│   └── TPK (2025) — 社会力模型+图注意力
│
├── 交互作为辅助任务
│   ├── SSL-Interactions (2024) — 4个pretext任务
│   │   ├── range gap prediction
│   │   ├── closest distance prediction
│   │   ├── direction of movement prediction
│   │   └── type of interaction prediction
│   └── Action-based Contrastive (2022) — 动作对比
│
└── 交互作为loss
    ├── Social Force Loss (跨界idea) — 可微社会力
    └── Collision Avoidance Loss — 避免碰撞
```

## 关键观察

1. **隐式交互 → 显式交互的迁移**：2022年后显式交互方法增长
2. **SSL-Int 的局限**：被动交互分类（"agent A和B有没有交互"），不建模"谁影响谁、影响多大"
3. **M2I 的启发**：influencer/reactor 分解→但没有把这个分解作为辅助loss
4. **因果交互的空白**：为什么没有辅助任务建模"agent A导致agent B变道"？

## 和辅助任务的关系

- 交互建模本身是预测任务的一部分
- **辅助交互的辅助任务**：通过额外的监督信号帮模型更好地学交互（如SSL-Int）
- 交互相关的辅助任务目前只有 SSL-Int 和 ActionCL
