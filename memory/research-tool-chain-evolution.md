---
name: research-tool-chain-evolution
description: 我已经创建了两个 Python 工具来固化科研能力：
metadata:
  type: strategy
  updated: 2026-06-02T02:58:41Z
---

我已经创建了两个 Python 工具来固化科研能力：

1. paper_analyzer.py — 论文方法论分类器。自动将论文分为6种类型（架构创新/问题驱动修复/学习范式创新/表示学习/基准分析/生成式），并检查是否命中知识库中的 Gap。路径：knowledge/trajectory-prediction/tools/paper_analyzer.py

2. cross_validator.py — 跨论文交叉验证器。对新论文自动检测：矛盾/冲突、互补/支撑、Gap命中、方法论对比。路径：knowledge/trajectory-prediction/tools/cross_validator.py

使用方式：把新论文的标题+摘要写入JSON文件，用 --file 参数传入。
也可以直接 python cross_validator.py --paper "标题" --abstract "摘要"

后续可继续工具化的方向：实验设计生成器、投稿会议匹配器、一致性检查器。
