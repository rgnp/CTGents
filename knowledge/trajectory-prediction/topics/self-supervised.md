# 子方向：自监督预训练（Self-supervised Pretraining）

> **核心问题**：如何利用大量无标签驾驶数据预训练轨迹预测模型。
> **空白等级**：⭐⭐（Plan-MAE已做，但还有拓展空间）

## 已有工作

| 论文 | 方法 | 局限 |
|---|---|---|
| Plan-MAE (arxiv 2025.07) | 3个MAE任务+1个规划任务 | 两阶段（pre-train+fine-tune），不是训练时辅助loss |
| PreCLN (Neurocomputing 2022) | 对比学习预训练 | 较老 |
| Contrastive MAE (RAS 2025) | MAE+对比 | 较新 |

## 空白

MAE预训练 + 训练时辅助loss 可以结合。
