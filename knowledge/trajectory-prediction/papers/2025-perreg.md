# PerReg+: Dual-Level Representation Learning for Trajectory Prediction

- **会议/年份**: CVPR 2025
- **作者**: Kaouther Messaoud, et al. (EPFL)
- **代码**: 未找到（可能开源）
- **阅读日期**: 2026-06-01
- **阅读状态**: CVPR 摘要 + 项目链接
- **链接**: https://cvpr.thecvf.com/virtual/2025/poster/33122
- **标签**: #表示学习 #自蒸馏 #掩码重建 #泛化

## 核心贡献

**现有问题**：轨迹预测模型跨数据集泛化差，复杂交互处理不好。

**核心思路**：双层级表示学习（局部细节 + 全局上下文）+ 自适应提示调优。

## 辅助任务设计

**类型**: 重构式 + 规正式（Template C + B）

**两个辅助学习信号**：

| 辅助任务 | 类型 | 作用 |
|---|---|---|
| Self-Distillation (SD) | 规正式 | 让局部表示和全局表示互相蒸馏，强制层级间一致性 |
| Masked Reconstruction (MR) | 重构式 | 掩码输入后重建 segment 级轨迹和车道段 |

**关键设计**：
- 用 Register 查询替代传统的聚类+非极大值抑制 → 更高效的多模态处理
- 适配调优阶段冻结主架构，只优化少量 prompt

## 实验结论

- 在 nuScenes、Argoverse 2、WOMD 上取得 SOTA
- 跨域泛化显著提升（与 UniTraj 的发现一致但提出了解法）
- SD 和 MR 各自贡献，消融验证

## 局限性（我的判断）

1. **和辅助任务论文的定位区别**：PerReg+ 是"用辅助任务改进表示"而非"用辅助任务改进特定缺失能力"
2. **规划兼容性没做**：虽然预测指标好，但没验证规划器是否受益
3. **代码可能不开源**→ 用作基线的难度大

## 与已有知识的关系

- 和 UniTraj 的跨域发现一致：PerReg+ 尝试解决 UniTraj 发现的问题
- 和 TrajCLIP 的区别：用自蒸馏而非对比学习
- **和我们的关系**：证明了 mask reconstruction 作为辅助任务的有效性

## 冲塔怪评价

（待你审查后填写）
