# 🗂 知识索引（Layer 1）

> 用途：对话开始时快速扫描，确认"我学过什么"。
> 维护：每次读新论文/发现新Gap后更新。
> 详见架构规范：[ARCHITECTURE.md](ARCHITECTURE.md)

---

## 已读论文（12 篇，按年份排序）

| 缩写 | 全称 | 会议 | 年份 | 一句话核心 | 文件 |
|:----:|:----|:----:|:----:|:-----------|:----:|
| HiVT | Hierarchical Vector Transformer for Multi-Agent Motion Prediction | CVPR | 2022 | 分层向量Transformer，局部+全局编码，实时预测 | 2022-hivt.md |
| ADAPT | Efficient Multi-Agent Trajectory Prediction with Adaptation | ICCV | 2023 | 自适应权重+动态预测头，比HiVT更快更准 | 2023-adapt.md |
| WhatTruly | What Truly Matters in Trajectory Prediction for Autonomous Driving | NeurIPS | 2023 | 证明预测精度(ADE/FDE)≠规划性能，dynamics gap | 待录入 |
| SSL-Int | Pretext Tasks for Interactive Trajectory Prediction | arXiv | 2024 | 4个交互感知pretext任务增强交互表示 | 2024-ssl-interactions.md |
| CCTR | Calibrating Trajectory Prediction for Uncertainty-Aware Motion Planning | AAAI | 2024 | 后处理校准预测置信度，提升规划安全性 | 2024-cctr.md |
| UniTraj | Unified Framework for Scalable Vehicle Trajectory Prediction | ECCV | 2024 | 统一benchmark，发现跨域泛化差 | 2024-unitraj.md |
| TrajCLIP | Pedestrian Trajectory Prediction via Contrastive Learning | NeurIPS | 2024 | 对比学习拉近历史/未来特征空间 | 2024-trajclip.md |
| LaKD | Length-agnostic Knowledge Distillation | NeurIPS | 2024 | 知识蒸馏教师→学生，不受输入长度限制 | 2024-lakd.md |
| DiffTORI | Differentiable Trajectory Optimization for Imitation Learning | NeurIPS | 2024 | 可微优化作为策略表征 | 待录入 |
| Plan-MAE | Self-supervised Pretraining for Integrated Prediction and Planning | arXiv | 2025 | MAE预训练：重构道路+轨迹+导航路线 | 2025-plan-mae.md |
| PFR-HiVT | Progressive Feature Refinement for HiVT | Symmetry | 2025 | 渐进式特征细化增强HiVT交互模块 | 2025-pfr-hivt.md |
| PerReg+ | Dual-Level Representation Learning for Trajectory Prediction | CVPR | 2025 | 自蒸馏+掩码重建做双层级表示学习 | 2025-perreg.md |

## 待读论文

| 论文 | 为什么待读 | 优先级 |
|:----|:----------|:------:|
| PiP: Planning-informed trajectory prediction | 预测→规划信息流 | ⭐⭐⭐ |
| Interactive Joint Planning (NVIDIA) | 交互式联合规划 | ⭐⭐⭐ |
| CaDeT (CVPR 2024) | 因果解纠缠 | ⭐⭐⭐ |
| Diffusion-Planner (ICLR 2025) | 扩散模型规划 | ⭐⭐ |
| CarPlanner (CVPR 2025) | RL轨迹规划 | ⭐⭐ |
| Entropy-Based Uncertainty Modeling | 不确定性量化与分解 | ⭐⭐⭐ |

---

## 空白方向（5 个 Gap）

| 编号 | 名称 | 等级 | 核心主张 |
|:----:|:----|:----:|:---------|
| Gap A | 规划兼容性辅助训练 | ✅ | 预测Loss中加规划可用性项 |
| Gap B | 训练期不确定性校准 | ✅ | 把ECE写成可微Loss端到端训练 |
| Gap C | 预测编码辅助任务 | ✅ | 加辅助头预测自己的预测误差 |
| Gap D | 闭环评估框架 | ✅ | 搭仿真评估预测→规划闭环 |
| Gap E | HiVT驱动的交互规划器 | 🔶 | HiVT编码+可微优化做联合训练 |

**等级**：✅ 确认 / 🔶 疑似 / ⚪ 推测 / 🆕 跨界验证

---

## 子方向速览

| 子方向 | 核心问题 | 代表论文 | 空白程度 |
|:-------|:---------|:--------|:--------:|
| 置信度校准 | 模型不知道预测有多可靠 | CCTR | ⭐⭐⭐ 训练期有空间 |
| 交互建模 | 模型不感知agent间影响 | SSL-Int | ⭐⭐⭐ 反事实方向空白 |
| 物理可行性 | 预测轨迹违反约束 | TPK(待读) | ⭐⭐⭐ 规划兼容性未做 |
| 规划兼容性 | 预测好但规划器用不了 | 暂无专门工作 | ⭐⭐⭐⭐⭐ 最空白 |
| 自监督预训练 | 如何无标签学习表示 | Plan-MAE, PerReg+ | ⭐⭐ 已有但可拓展 |
| 表示学习 | 隐空间缺乏语义结构 | TrajCLIP, PerReg+ | ⭐⭐⭐⭐ 可深入 |
| HiVT架构演进 | HiVT怎么改进 | ADAPT, PFR-HiVT | ⭐⭐ 架构改进空间有限 |

---

## 跨界假说

| 编号 | 来源 | 假说 | 验证状态 |
|:----:|:----:|:-----|:--------:|
| H1 | 认知神经科学 | 预测编码辅助任务 | ✅ 真空白 |
| H2 | 最优控制 | 规划器反向传播Loss | ⚡ 差异化空白 |
| H3 | OOD检测 | 场景级OOD感知 | 🔶 待验证 |

---

## 工具链

| 工具 | 功能 | 路径 |
|:-----|:-----|:-----|
| paper_analyzer.py | 方法论分类 + Gap匹配 + 卡片模板 | tools/paper_analyzer.py |
| cross_validator.py | 跨论文矛盾/互补/方法论对比 | tools/cross_validator.py |

---
---

## 工具链

| 工具 | 功能 | 路径 |
|:-----|:-----|:-----|
| paper_analyzer.py | 方法论分类 + Gap匹配 + 卡片模板 | tools/paper_analyzer.py |
| cross_validator.py | 跨论文矛盾/互补/方法论对比 | tools/cross_validator.py |
| cache_diag.py | 缓存健康诊断：前缀膨胀检测 | tools/cache_diag.py |


## 方法论速查

| 类型 | 代表 | 风险 | 适合会议 |
|:-----|:-----|:----:|:--------:|
| 架构创新 | HiVT, ADAPT | 中 | CVPR, ICCV, NeurIPS |
| 问题驱动修复 | **CCTR** | 低 | AAAI, ICRA, IROS, ITSC |
| 学习范式创新 | SSL-Int, Plan-MAE, TrajCLIP | 中低 | NeurIPS, ICLR, CoRL |
| 表示学习 | PerReg+, TrajCLIP | 中 | CVPR, NeurIPS |
| 基准/分析 | UniTraj, WhatTruly | 低 | ECCV, Workshop |
| 生成式 | MotionDiffuser | 中高 | NeurIPS, ICLR |
