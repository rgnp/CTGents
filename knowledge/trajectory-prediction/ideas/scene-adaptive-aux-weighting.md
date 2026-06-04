# 场景自适应辅助任务加权（Scene-Adaptive Auxiliary Weighting）

> 状态：💡 初步想法
> 来源：调研PTA时思考loss权重 → 不同场景需要的辅助信号不同

---

## 核心想法

现有辅助任务（包括PTA）对**所有场景用同样的权重**。但不同场景对不同辅助信号的依赖程度不同：

| 场景类型 | 短期辅助 | 目的地辅助 | 意图辅助 |
|:--------|:--------:|:---------:|:--------:|
| 高速直行（确定性高） | 高 | 低 | 低 |
| 交叉口（多模态强） | 中 | 高 | 高 |
| 密集交互（频繁变道） | 中 | 中 | 高 |
| 环岛（复杂拓扑） | 低 | 高 | 中 |

## 具体形式

```python
# 场景特征 → 权重预测
scene_feat = get_scene_embedding(data)  # 从local encoder提取
weight_net = MLP(d_model, 3)  # 预测3个aux_loss的权重
scene_weights = torch.sigmoid(weight_net(scene_feat))  # [batch, 3]

# 加权辅助损失
total_aux_loss = (scene_weights[:, 0] * short_loss +
                  scene_weights[:, 1] * dest_loss +
                  scene_weights[:, 2] * intent_loss)
```

## 和PTA的关系

- PTA的固定权重（0.1/0.1/0.05）是合理起点
- 场景自适应加权是**PTA的增强版**，复杂度增加但可能效果更好
- 优先级：先做PTA固定权重，再考虑增强

## 关联

- CombAux (ICLR 2026) 用了全局自适应加权（全局温度参数）
- 场景自适应比全局自适应更细致，但实现也更复杂
