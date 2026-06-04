# 交互困难度感知采样 / 加权（Interaction-Difficulty Aware Training）

> 状态：💡 初步想法
> 来源：调研PTA时注意到——不是所有场景都需要辅助任务，有些场景本身已经很简单

---

## 核心想法

轨迹预测的样本**难度高度不均衡**：大部分场景是简单的直行跟车，少数场景是复杂的交叉口/密集交互/突发情况。模型花同样的计算量在简单和困难样本上，导致困难样本拟合不足。

## 具体形式

### 方案A：困难场景加权采样
```
1. 定义交互难度指标（agent间距变化率、车道变化次数、交互agent数量）
2. 按难度分层采样（困难场景采样率>简单场景）
3. 或在loss中按难度加权
```

### 方案B：辅助任务只应用于高难度场景
```
if interaction_difficulty > threshold:
    total_loss += aux_losses
else:
    total_loss = main_loss_only
```

## 关联

- SSL-Interactions 提了"交互场景筛选"但用于pretext任务的数据构造，不是训练加权
- 这个idea可以和PTA组合（PTA + 困难感知训练）

## 风险

- 需要定义合理的"困难度"指标
- 可能让模型忽视简单场景的精度
