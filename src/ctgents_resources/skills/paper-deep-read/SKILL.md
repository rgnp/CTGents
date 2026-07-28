---
name: paper-deep-read
description: 执行单篇论文的结构化深读与论证审计。仅在 active paper-deep-read Psyche 判断需要一次性分析 Claim、Grounds、Warrant、Qualifier、研究设计或证据边界时使用；不用于渐进带读。
---

# 论文结构化深读

## 前置条件

- `paper-deep-read` Psyche 必须已激活；未激活时拒绝执行。
- 本 Skill 不加载任何 Psyche，也不根据论文 URL、关键词或文件类型自行触发。
- 在形成实质判断前取得论文正文；摘要只能支持扫描级结论。

## 轴选择

- `depth`：用户未指定时用 `normal`。
- `paper_type`：根据论文实际论证结构判断；证据不足时用 `research` 并标明推断。
- `domain`：根据论文内容选择；不确定时用 `general-ml`。领域轴只加载执行片段，不加载领域 Psyche。

激活后读取 manifest 声明的 always-load 内容和命中轴片段，执行其中的 P0→P5 流程。

## 执行约束

- 事实、论文原文、分析推理和猜测分层。
- “论文声称 X”必须能定位到原文；无法定位时降级表述。
- 重点审计证据为何足以支持主张，不把指标提升直接等同于方法成立。
- 明确 claim 的适用范围、研究设计威胁和作者未检验的假设。
- 需要跨论文或领域知识时查询 Knowledge；不要把 Psyche core 当作事实库。
- 输出粒度服从 depth 轴，不重复输出 Skill、Psyche 和参考文件中的同一套框架。

## 收尾

输出核心 claim、证据链、最弱 warrant、qualifier、研究设计边界、领域位置和可复用连接。只有用户需要时才落盘论文卡片；落盘路径服从当前项目结构，不由 Skill 擅自创建新的知识体系。
