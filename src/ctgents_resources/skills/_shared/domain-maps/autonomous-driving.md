# 自动驾驶领域 — 领域地图

> 定位: 跨 skill 共享的领域知识索引
> 使用: paper-deep-read skill P2 阶段定位论文时查阅

---

## 子领域速查

### 轨迹预测 (Trajectory Prediction)
- 任务: 给定历史轨迹 + 场景上下文 → 预测未来轨迹
- 关键指标: minADE/minFDE/MR
- 核心 tension: 多模态（驾驶的不确定性） vs 确定性回归
- 用户关注: PIM-MomAD、对偶时序注意力

### Occupancy 预测 (Occupancy Forecasting)
- 任务: 给定历史 occupancy → 预测未来 occupancy 或补全静态
- 关键指标: IoU/mIoU
- 核心 tension: 4D (3D + time) 存储/计算开销 vs 表示保真度
- 用户关注: OccWorld、CascadeOcc

### 世界模型 (World Model)
- 任务: 学习环境动态的生成模型，rollout 未来状态
- 关键指标: 预测质量 + 下游任务性能
- 核心 tension: 生成式（高保真慢）vs 自回归（快但误差累积）
- 用户关注: DrivingGen、GAIA-1/2

### 生成式仿真 (Generative Simulation)
- 任务: 生成逼真的驾驶场景用于训练/测试
- 与 WM 的区别: 仿真强调场景多样性+可控性，WM 强调动态保真度
- 用户关注: 待探索

### 端到端自动驾驶 (End-to-End AD)
- 任务: sensor → control，不经过模块化 pipeline
- 核心 tension: open-loop IL 简单但不闭环 robust
- 用户关注: 待定

---

## 用户的硬约束（任何方法必须过）

1. 算力: 单人 A40 48GB 或 RTX 3090/4090 24GB
2. 不开源的可能不可复现（除非有完整复现说明）
3. 纯开环评估的结论需谨慎推广到闭环

---

## 链接

- 问题库: `knowledge/autonomous-driving/problems.md`
- 积木库: `knowledge/autonomous-driving/blocks.md`
- 进度: `knowledge/autonomous-driving/progress.md`
