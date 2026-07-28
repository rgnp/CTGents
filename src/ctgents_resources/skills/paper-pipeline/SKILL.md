---
name: paper-pipeline
description: >
  论文入库自动化流水线——候选发现→下载→转写→结构化入库→关联索引。
  仅在 active paper-collection Psyche 判断需要批量补论文时使用。
  支持 full（全流程 0→4A）、discover（仅搜索候选）、resume（断点续跑）三种模式。
---

# 论文入库流水线

## 前置条件

- `paper-collection` Psyche 必须已激活；未激活时拒绝执行。
- 本 Skill 不加载 Psyche，不根据关键词自行触发。
- 状态唯一真相来源：`knowledge/paper/_pipeline_state.md` + 各论文 `_meta.json`。
  不看目录内容推断阶段。

## 轴选择

- `stage`：默认 `full`。
  - `full`：阶段 0→1→2→3→4A，全自动。
  - `discover`：仅阶段 0（搜索+筛除+候选创建），不做下载。适用用户想先看有哪些新论文。
  - `resume`：从 `_pipeline_state.md` 读到断点继续。适用上一次中断后恢复。

## 核心约束

- 每阶段先查状态再执行。产物已存在且通过质量门 → 跳过。
- 每篇论文独立失败。3 篇成功 2 篇失败 → 成功的推进，失败的留原阶段 + 记录原因。
- 下载限速：每会话最多 10 篇，每篇最多 2 次重试间隔 60s。
- 阶段 0 不做评分——`ingest=pending`，等读到原文（阶段 3）再做入库决策。
- 阶段 4A 在阶段 3 完成 ≥1 篇新入库后整批重建。

## 执行流程

详见 `static/core/workflow.md`。总览：

```
phase 0          phase 0.5           phase 1          phase 2          phase 3          phase 4A
 搜索+筛除  →  分档(四档)     →  下载 PDF     →   全文转写    →   入库决策+评分  →  重建 _associations.json
 创建候选       core/增量/精华/跳过   2次重试/60s       paper.md         _meta.json         关联索引
 _meta.json     read_papers           限10篇/会话       质量检查         ingest yes/no
 (ingest       批量读摘要
  =pending)    精华→写_mini.md
```

每个阶段的结果汇报按 `references/report-template.md` 模板输出。

## 收尾

- `stage=full`：产出摘要报告（入库 / 排除 / pending 数量 + 失败原因）
- `stage=discover`：列出候选清单，提示哪些需要用户确认 domain 后进入下载
- `stage=resume`：汇报续跑结果 + 仍卡住的论文和原因
