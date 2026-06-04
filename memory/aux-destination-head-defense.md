---
name: aux-destination-head-defense
description: 审稿人会质疑"目的地辅助头只是把最后一帧loss又加了一遍"
metadata:
  type: strategy
  updated: 2026-06-03T08:54:29Z
---

审稿人会质疑"目的地辅助头只是把最后一帧loss又加了一遍"。必须提前准备好反驳逻辑：主任务的WTA regression通过Decoder反向传播（非线性+仅winner模式），而目的地头直接L1作用于Global Encoder输出（全梯度+所有样本）。两者梯度路径不同。实验上必须加 final-frame loss reweighting 对照来实证证明目的地头不同。【来自PTA评审v1教训】
