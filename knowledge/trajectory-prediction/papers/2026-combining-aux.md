# Combining Auxiliary Losses for Safer and More Robust Trajectory Prediction

| 字段 | 内容 |
|:----|:-----|
| **会议** | ICLR 2026 |
| **任务** | 车辆轨迹预测 + 辅助loss组合 |
| **数据集** | nuScenes, Argoverse 2 |

## 核心贡献

1. 引入三个辅助loss：Offroad Loss + Direction Consistency Loss + Diversity Loss
2. **关键发现**：单个loss效果有限，**只有组合**才能产生鲁棒的道路合规预测
3. 提出轻量级自适应加权方案，自动平衡各辅助loss

## 实验结果

- 43% off-road error 下降（平均）
- 在 SceneAttack 对抗攻击下也有鲁棒性提升
- 不牺牲精度（ADE/FDE 没有明显下降）

## 对你的意义

| 维度 | 分析 |
|:----|:-----|
| **这个路线还能不能做** | ❌ 道路合规性辅助任务方向已被完整覆盖 |
| **学到了什么** | 辅助任务组合 > 单个辅助任务；自适应加权重要 |
| **差异化方向** | 做**其他类型**的辅助任务组合（时间/校准/交互），而不是道路合规 |
