# 论文入库流水线 — 操作流程

> 加载: always | 定位: 操作层执行序列
> 规范文档: `knowledge/paper/_paper_pipeline_spec.md`
> 状态追踪: `knowledge/paper/_pipeline_state.md`

---

## 执行前（每次必做）

**1. 加载状态**

读 `knowledge/paper/_pipeline_state.md` 全文。从中提取：
- 所有论文 slug 及其当前 phase
- 阶段 0 中哪些是 `pdf_pending=true`（下载失败过的）
- 阶段 0 中哪些是空目录（新候选，无 `_meta.json`）
- 已排除论文列表（ingest=no，不要重复处理）

**2. 确定目标**

- `stage=full`：所有 phase<3 且 ingest≠no 的论文
- `stage=discover`：只做新搜索，已有论文不重处理
- `stage=resume`：所有 phase<3 的论文从当前阶段继续

**3. 网络检查**

不做独立预检。阶段 0 的 `search_web` / 阶段 1 的 `fetch_paper` 首次失败即视为
网络不可达。（有 run_python 的会话可选 socket 预检；无人值守心跳环境没有
run_python，靠工具失败信号即可。）

网络不可达时：阶段 0/1 跳过，阶段 2/3 可以用已有 PDF/paper.md 推进。

---

## 阶段分派决策树

对每篇待处理论文，按 `_meta.json` 的 `phase` + `pdf_pending` + `download_error` 判断：

```
phase=0, pdf_pending=true, download_error 存在 → 网络恢复后重试下载（跳阶段 0 搜索和 0.5 分档）
phase=0, pdf_pending=false, triage 未设置（空目录）   → 从阶段 0 开始 → 阶段 0.5 分档
phase=0.5, triage=core                 → 从阶段 1 下载开始
phase=0.5, triage=incremental           → 从阶段 1 下载开始，下载后暂停（不自动进阶段 2）
phase=0.5, triage=card                  → 已完成（精华卡已写），跳过。用户要求时可升级
phase=0.5, triage=skip                  → 跳过（已排除）
phase=1（有 PDF，无 paper.md）          → 从阶段 2 转写开始
phase=2（有 paper.md，无 _meta.json 或 ingest=pending）→ 从阶段 3 结构化开始
phase=3（已完成）                       → 跳过
ingest=no                               → 跳过（已排除）
```

**关键**：`pdf_pending=true` 且有 `download_error` 的论文 ≠ 没处理过的新论文。它已经过了阶段 0（搜索+筛除），只是下载失败。不要重复搜索它。

---

## 阶段 0 · 搜索与候选创建

仅当 `stage=discover` 或 `stage=full` 且有新关键词/domain 需要搜索时执行。

### 搜索

用 `search_web(query, depth="scholar")` 搜索。搜完用 `learn` 补漏。

搜索词 = `"{domain} {keywords} 2025 2026"`。domain 来自用户指定（如 `latent-world-model`），keywords 来自用户补充。

### 去重与筛除

对每个搜索结果：
1. arxiv ID 或标题在 `_pipeline_state.md` 主表中已存在 → 跳过
2. arxiv ID 或标题在排除记录中 → 跳过
3. 标题/摘要明确触发排除规则（见 `_paper_pipeline_spec.md` 第六节）→ 记录排除理由，跳过
4. 通过 → 进入候选

排除规则速查：
- VLA / VLM / LLM → 排除（仅作 backbone 的不排除）
- 视频生成世界模型（生成像素级未来帧）→ 排除
- 3D Gaussian / Gaussian Splatting → 排除
- RL 路线（Dreamer 系列）→ 排除
- 纯感知/检测/分割（不涉及规划或预测）→ 排除
- 非自动驾驶 → 排除

### 创建候选

对每个通过的论文：
1. 生成 paper-slug：`{第一作者小写姓}-{年份}-{关键词}`
2. 创建目录 `knowledge/paper/{slug}/`
3. 写入最小 `_meta.json`：

```json
{
  "arxiv_id": "YYMM.NNNNN",
  "title": "从搜索结果提取的标题",
  "phase": 0,
  "triage": "pending",
  "info_level": "snippet",
  "ingest": "pending",
  "tags": [],
  "domain": "TBD",
  "pdf_pending": true,
  "date_added": "YYYY-MM-DD"
}
```

4. 更新 `_pipeline_state.md`——新候选加入阶段 0 列表

### 阶段 0 收尾

`stage=discover` 在此结束。输出候选清单，提示哪些需用户确认 domain。

---

## 阶段 0.5 · 分档（Triage）

阶段 0 产出候选清单后，在下载前必须分档。用 `read_papers` 批量读取 arxiv 摘要，
每篇候选按以下标准分入三档：

### 分档标准

| 档位 | 标准 | 处理方式 |
|------|------|----------|
| **🔴 核心** | 边界内 + 方向直接命中 + 摘要暗示真正方法贡献 | 下载 PDF → 转写 → 结构化评分 |
| **🟡 增量** | 边界内但方法看起来是工程堆叠 / 方向边缘 / 不确定——但有参考价值 | 下载 PDF 存着，暂不转写——等用户判断或闲时再处理 |
| **🟠 精华** | 边界模糊但有亮点 / 方法有趣但方向不完全匹配 / 方法论参考价值 | 不下载 PDF，写 10 行精华卡（`_mini.md`）——以后 rag_search 可命中，需要时再升级 |
| **⚪ 跳过** | 边界外 / 已有多篇同方向覆盖 / 摘要一看就不行 | 不下载，不写卡，记录排除理由 |

### 分档判断依据（仅凭标题+摘要，不等全文）

1. **边界检查**：看摘要中的方法关键词——出现 VLM/LLM CoT/视频生成/RL/Gaussian → 大概率边界外
2. **方向匹配**：摘要是否提到 latent/occupancy world model / BEV prediction / motion forecasting for planning？
3. **方法信号**："we propose a novel..." vs "we extend..." / "we combine..." ——前者可能是核心，后者大概率增量
4. **已有覆盖**：这个方向在 knowledge/paper-deep-read/ 里是否已有 2+ 篇深读？→ 可能跳过

### 执行步骤

1. 收集所有阶段 0 候选的 arxiv_id
2. `read_papers(ids=[...])` 批量读取标题+摘要（一次调 30 篇以内）
3. 每篇候选标注 `triage` 字段到 `_meta.json`：
   - `"triage": "core"` — 进阶段 1 下载
   - `"triage": "incremental"` — 进阶段 1 下载，`phase` 保持 0.5，暂不进转写
   - `"triage": "card"` — 不进阶段 1，写精华卡 `_mini.md`
   - `"triage": "skip"` — 不进阶段 1，记录排除理由
4. 汇报分档结果给用户——「N 篇核心 / M 篇增量 / P 篇精华 / K 篇跳过」
5. 用户可以手动调档

### 分档汇报格式

```
## 分档结果 — 共 X 篇候选

### 🔴 核心（N 篇）→ 立即下载+转写
| # | slug | 一句话 | 为什么核心 |
|---|------|--------|-----------|
| 1 | ... | ... | ... |

### 🟡 增量（M 篇）→ 下载存着
| # | slug | 一句话 | 为什么增量 |
|---|------|--------|-----------|
| 1 | ... | ... | ... |

### 🟠 精华（P 篇）→ 写卡不下载
| # | slug | 一句话 | 为什么精华卡 |
|---|------|--------|------------|
| 1 | ... | ... | ... |

### ⚪ 跳过（K 篇）→ 不下载
| # | slug | 一句话 | 跳过理由 |
|---|------|--------|----------|
| 1 | ... | ... | ... |
```

### 精华卡格式（`_mini.md`）

对 `triage=card` 的论文，在 `knowledge/paper/{slug}/` 下只建一个 `_mini.md`（无 PDF）：

```markdown
# {标题}

> arXiv: {id} | {年份} | {会议/期刊} | {作者列表}

## 一句话
{基于摘要的一句话总结}

## 核心贡献
- {贡献1}
- {贡献2}
- {贡献3}

## 为什么没深读
{方向边缘 / 工程堆叠 / 已有覆盖 / 等用户确认 / ...}

## 用户相关度
{如果未来需要，这篇能用来做什么——方法借鉴/对比baseline/问题验证/...}

## Tags
`{tag1}` `{tag2}` `{tag3}`
```

同时写好最小 `_meta.json`：`phase=0.5, triage=card, info_level=abstract, ingest=card`。

精华卡不评分，不下载 PDF。以后 `rag_search` 可命中——需要时再从卡判断是否"升级"为下载。

### 分档后

- 🔴 核心 → 进入阶段 1 下载
- 🟡 增量 → 进入阶段 1 下载，下载完成后停在此阶段，不自动进阶段 2
- 🟠 精华 → 写 `_mini.md`，不进阶段 1。`_meta.json` 设 `triage=card, phase=0.5`
- ⚪ 跳过 → 写入 `_pipeline_state.md` 排除记录，从阶段 0 移除

---

## 阶段 1 · 下载 PDF

### 限速

- 每会话最多下载 10 篇
- 每篇最多重试 2 次，间隔 60s
- 两篇之间间隔 30s

### 对每篇 phase=0 的论文

用专用工具下载（PDF 魔数/大小校验内置，坏内容不落盘）：

```
fetch_paper(source=arxiv_id, dest=f"knowledge/paper/{slug}/paper.pdf")
```

失败（返回 ❌）→ 按重试规则再试或换下一篇；不要改用 run_python 写下载代码——
fetch_paper 就是这一步的正道，无人值守环境也只有它。

### 失败处理

- 下载成功 → 更新 `_meta.json`：`phase=1`，移除 `pdf_pending`
- 下载失败（2次重试仍失败）→ 在 `_meta.json` 记录 `download_error: "arxiv unreachable YYYY-MM-DD"`，保持 `phase=0, pdf_pending=true`
- 下一会话 `resume` 时自动重试

### 更新状态

`_pipeline_state.md` 中该论文从阶段 0 移到阶段 1（或保持在阶段 0 标记失败）。

---

## 阶段 2 · 全文转写

### 对每篇 phase=1 的论文

用专用工具转写（逐页，页头 `## Page N`）：

```
transcribe_paper(src=f"knowledge/paper/{slug}/paper.pdf",
                 dest=f"knowledge/paper/{slug}/paper.md")
```

不要改用 run_python 写 fitz 代码——transcribe_paper 就是这一步的正道，无人值守环境也只有它。

### 质量检查

- [ ] paper.md 行数 > 200
- [ ] 包含 "Abstract" 或 "摘要"
- [ ] 包含 "Introduction" 或 "1." 或 "引言"
- [ ] 包含 "Conclusion" 或 "References" 或 "参考文献"

不通过 → 标记 `parse_error: "原因"`，保持 `phase=1`，不阻塞其他论文。

### 更新状态

通过 → `_meta.json` 设 `phase=2`。`_pipeline_state.md` 同步更新。

---

## 阶段 3 · 结构化 + 入库决策

### 对每篇 phase=2 的论文

**3.1 读原文**

用 `read_file` 读 `paper.md` 的 Abstract + Introduction + Conclusion。

**3.2 入库决策**（回答三个问题）

1. 在用户边界内吗？latent/occupancy WM？端到端规划？→ 不在 → `ingest=no, domain=excluded`
2. 纯工程堆叠吗？只是换 backbone 或调 loss？→ 是 → `ingest=no`
3. 有真正的方法贡献吗？→ 有 → `ingest=yes`

**每个决策附带一句理由**（如 "occupancy WM + 非自回归预测 → 边界内"）。

**3.3 确定 info_level + 评分**

| 能读到什么 | info_level | 入库决策 | 评分 |
|------------|-----------|:---:|------|
| 仅有搜索结果 | `snippet` | 保持 pending | ❌ |
| 读到摘要 | `abstract` | 可初步判断 | novelty/experiment/reproducibility 填 `-1` |
| 读到 paper.md | `paper_md` | ✅ 确定 | 五维度均可填 |

评分五维度（详见 `_paper_pipeline_spec.md` 第三节）：
- 相关度 (0.35)、方法新颖度 (0.15)、发表质量 (0.20)、实验说服力 (0.20)、可复现性 (0.10)
- 无法判断的维度填 `-1`（不是 0）
- 综合分 = Σ(有效维度分 × 权重) / Σ(有效维度权重)
- ≥7.0 自动入库，5.0-6.9 标记"待人工判断"，<5.0 排除

**3.4 写入 _meta.json**

对 `ingest=yes` 的论文：

```json
{
  "arxiv_id": "YYMM.NNNNN",
  "title": "...",
  "authors": ["..."],
  "year": 2026,
  "venue": "...",
  "phase": 3,
  "info_level": "paper_md",
  "ingest": "yes",
  "score": 7.5,
  "score_confidence": "±0.5",
  "score_detail": {
    "relevance": 8, "novelty": 7, "venue_quality": 8,
    "experiment": 7, "reproducibility": -1
  },
  "tags": ["latent-wm", "bev", "autoregressive"],
  "domain": "latent-world-model",
  "date_updated": "YYYY-MM-DD"
}
```

对 `ingest=no` 的论文：`domain=excluded`，不评分，追加排除记录到 `_pipeline_state.md` 排除表中（含理由+日期）。

**3.5 质量门**

- [ ] `ingest` 不是 `pending`（除非 PDF 确实下载失败）
- [ ] `ingest=no` → `domain=excluded` + 排除原因
- [ ] `ingest=yes` → `info_level ≥ abstract`，score 存在
- [ ] domain 非空且合法
- [ ] tags ≥ 3 个（excluded 除外）

### 更新状态

`_pipeline_state.md` 更新：完成论文从阶段 2 移到阶段 3（或排除表）。

---

## 阶段 4A · 重建关联索引

### 触发条件

阶段 3 完成后有 ≥1 篇新论文入库（ingest=yes）→ 整批重建 `_associations.json`。

本阶段需要 run_python（关联图计算）。无人值守心跳环境没有 run_python：跳过本阶段，在 `_pipeline_state.md` 记一行「4A 待重建（N 篇新入库）」，留给有人会话执行；不要用 write_file 手搓 _associations.json。

### 重建逻辑

```python
# 1. 加载所有 ingest=yes 的论文 _meta.json → 提取 domain/tags/arxiv_id/score
# 2. 扫描所有 paper.md → 提取 benchmark 名称
#    bench_patterns = ["nuScenes", "NAVSIM", "Waymo", "CARLA", "nuPlan",
#                      "KITTI", "Argoverse", "Bench2Drive", "HUGSIM"]
# 3. 扫描所有 paper.md → 用正则 r'\d{4}\.\d{4,5}' 提取 arXiv ID →
#    匹配已知论文 slug → 构建 cites/cited_by
# 4. 对每对论文计算关系：
#    - 同 domain: +3
#    - 共享 tags: +N（交集大小）
#    - A cites B 或 B cites A: +5
#    - 共享 benchmark: 有交集 +2
# 5. 结果写入 _associations.json（全量覆盖，非增量）：
#    {papers: {slug: {domain, tags, score, benchmarks, cites, cited_by}},
#     domain_groups: {domain: [slugs]},
#     relations: [{papers: [a, b], types: [...], weight: N}]}
```

论文数 ≤ 50 所以 O(n²) 全对计算可行，不需要优化。

### 更新状态

`_pipeline_state.md` 中阶段 4A 的时间戳更新。

---

## 阶段 4B · 领域全景图

**不在 paper-pipeline 自动执行范围内。**

触发条件（手动）：
1. 同 domain 累积新增 ≥5 篇
2. 用户明确要求

在收尾报告中提醒用户是否满足触发条件即可。

---

## 收尾报告

流水线完成后（或中断后），按此结构汇报：

```
# Paper Pipeline 执行报告 — YYYY-MM-DD

## 结果
- 入库 N 篇（新入库 / 从 pending 推进）
- 排除 M 篇（含原因）
- Pending K 篇（下载失败 / parse_error）
- 跳过 L 篇（已完成，无需处理）

## 入库论文
| slug | title | domain | score |
|------|-------|--------|-------|
| ... | ... | ... | ... |

## 排除论文
| slug | 原因 | 日期 |
|------|------|------|
| ... | ... | ... |

## Pending 论文
| slug | 失败原因 | 下次如何处理 |
|------|----------|-------------|
| ... | download_error: arxiv unreachable | 网络恢复后 resume 自动重试 |
| ... | parse_error: PDF 无文字层 | 手动下载或找替代源 |

## 可触发的后续
- _associations.json 已重建（N 条关系，M 篇论文）
- domain X 累积新增 ≥5 篇 → 可触发阶段 4B 领域全景图
```

报告口头输出，是否落盘 `knowledge/paper/_pipeline_report_{date}.md` 取决于用户是否要求。
