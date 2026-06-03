---
name: research-architecture-three-layer
description: 科研能力三层架构 v1.0 已建立：
metadata:
  type: strategy
  updated: 2026-06-02T03:07:55Z
---

科研能力三层架构 v1.0 已建立：

L1 理解层（我/AI）：读论文→总结→发现Gap→生成假说→跨界联想→讲故事。语义理解+创造性推理，不可工具化。7项能力。

L2 工具层（Python脚本）：方法论分类、Gap关键词匹配、跨论文交叉验证、卡片模板生成。确定性代码，一次编译反复调用。当前有 paper_analyzer.py 和 cross_validator.py。

L3 数据层（Markdown文件）：KNOWLEDGE_INDEX.md（扁平索引）、papers/*.md（论文卡片）、TAXONOMY.md（6维×5模板矩阵）、meta/（Gap/假说/死路/日志）、topics/（子方向深度分析）。

启动流程：ARCHITECTURE.md → KNOWLEDGE_INDEX.md → 按需深入。核心原则：理解归我、机械归工具；先索引再细节；工具不替代我、我驱动工具。

架构文件：D:\project\knowledge\trajectory-prediction\ARCHITECTURE.md
