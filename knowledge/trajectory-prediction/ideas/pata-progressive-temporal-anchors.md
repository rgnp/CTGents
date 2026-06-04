> 状态：✅ 通过评审，可进入实验 | 优先级：🥇
> 前身：PTA (Progressive Temporal Auxiliaries)，v1→v2 评审后定稿
> 状态：🔄 v1→v2 修改中 | 优先级：🥇
> 前身：PTA (Progressive Temporal Auxiliaries)，v1评审后改名重构
> 评审记录：[reviews/pta-review-v1.md](reviews/pta-review-v1.md)

---

## 核心变化（v1→v2）

| 维度 | v1（原PTA） | v2（PATA） | 原因 |
|:----|:-----------|:-----------|:-----|
| 命名含义 | Progressive Temporal Auxiliaries | **Progressive Auxiliary Temporal Anchors** | "渐进"指时间尺度递增，不是训练阶段 |
| 时间辅助 | 3帧短期(0.3s) + 目的地(3.0s) | **多尺度锚点：0.5s/1.0s/2.0s/3.0s** | 0.3s太短像速度估计 |
| 意图头 | 核心贡献之一 | **降为Phase 3可选** | 标签噪声大，不做主卖点 |
| 实验对照 | 缺少重点对照 | **加 final-frame reweighting** | 排除"重复监督"质疑 |
| 评价指标 | minADE/minFDE | **+ per-timestep ADE曲线** | 直接证明长时漂移缓解 |
| 创新性声称 | "完全空白" | **收敛措辞** | 规避审稿风险 |

## 辅助头设计（v2）

| 锚点 | 位置 | 输出 | Loss | λ |
|:----|:-----|:----|:----|:-:|
| 0.5s anchor | Local Encoder后 | 未来5帧位置 | L1 | 0.05 |
| 1.0s anchor | Global Encoder后 | 未来10帧位置 | L1 | 0.05 |
| 2.0s anchor | Global Encoder后 | 未来20帧位置 | L1 | 0.1 |
| 3.0s destination | Global Encoder后 | 最终帧位置 | L1 | 0.1 |
| intent（可选） | Global Encoder后 | 5类意图 | CE | 0.05 |

## 实施路线

```
Phase 1: PATA-Destination（仅3.0s anchor）
  对照: baseline + final-frame reweighting
  验证: FDE↓是否显著优于reweighting

Phase 2: PATA-MultiAnchor（4个锚点全开）
  验证: multi-anchor是否优于single destination

Phase 3: +Intent（可选）
  前提: 标签准确率>80%
```

## 完整方案

见 [`../meta/deep-dive-progressive-temporal.md`](../meta/deep-dive-progressive-temporal.md)
