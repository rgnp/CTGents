# 🧬 科研能力架构 v1.0

> 本文档是知识库的入口。每次对话开始时应先读此文档。
> 它定义了：三层架构、启动流程、工具使用、文件关系。

---

## 一、架构总览

```
┌─────────────────────────────────────────────────────┐
│                L1: 理解层（我 / AI）                  │
│                                                      │
│  读论文 → 总结 → 发现Gap → 生成假说 → 跨界联想       │
│  这些是语义理解 + 创造性推理，不可工具化              │
│                                                      │
│  输入：papers/*.md 的原始内容                        │
│  输出：新的论文卡片、更新的 gaps、方向建议            │
├─────────────────────────────────────────────────────┤
│                L2: 工具层（Python 脚本）              │
│                                                      │
│  分类 → 匹配 → 检查 → 对比 → 生成模板               │
│  这些是机械性重复劳动，用确定性代码执行              │
│                                                      │
│  paper_analyzer.py   — 方法论分类 + Gap匹配 + 卡片模板│
│  cross_validator.py  — 跨论文矛盾/互补/方法论对比     │
│  （待建）consistency_checker.py — 知识库一致性检查   │
├─────────────────────────────────────────────────────┤
│                L3: 数据层（Markdown 文件）            │
│                                                      │
│  论文卡片、Gap记录、分类体系、演化日志               │
│  这些是持久化的知识，结构化管理                      │
│                                                      │
│  KNOWLEDGE_INDEX.md — 已学内容的扁平索引              │
│  papers/*.md        — 每篇论文的结构化卡片            │
│  TAXONOMY.md        — 6维能力×5模板的idea生成矩阵     │
│  meta/              — Gap/假说/死路/演化日志          │
│  topics/            — 按子方向的深度分析              │
│  tools/             — 工具脚本                        │
└─────────────────────────────────────────────────────┘
```

### 核心原则

| 原则 | 含义 |
|:-----|:-----|
| **理解归我，机械归工具** | 读论文、找Gap、生成假说 = 我；分类、匹配、查重 = 工具 |
| **先索引，再细节** | 日常对话只看 KNOWLEDGE_INDEX.md；需要细节再读 papers/*.md |
| **一次编译，反复调用** | 方法论分类逻辑写死在 paper_analyzer.py 里，不再每次推理 |
| **工具不替代我，我驱动工具** | 我决定"什么时候跑工具"，工具只负责执行确定性计算 |

---

## 二、启动流程（每次对话开始时）

```
Step 1: 扫 ARCHITECTURE.md（本文档）
         ↓ 理解当前架构状态

Step 2: 扫 KNOWLEDGE_INDEX.md
         ↓ 确认"我学过什么"，更新 mental model

Step 3: 判断对话类型
         ├── 方向性讨论 → 读 meta/gaps.md + TAXONOMY.md
         ├── 具体论文分析 → 读对应 papers/*.md
         ├── 读新论文 → 运行 paper_analyzer.py → cross_validator.py
         └── 知识库维护 → 按需更新文件

Step 4: 执行对话
         ↓

Step 5: 对话结束时自检
         ├── 有新发现？→ 更新 KNOWLEDGE_INDEX.md + 相关文件
         ├── 有新工具？→ 更新 tools/ + 本文档
         ├── 有架构变化？→ 更新本文档
         └── 无变化？→ 不做冗余更新
```

---

## 三、文件地图

### 入口层（对话开始时读）

| 文件 | 作用 | 何时读 |
|:-----|:-----|:------|
| **ARCHITECTURE.md** | 架构总纲，定义全局规则 | 每次对话开始 |
| **KNOWLEDGE_INDEX.md** | 所有已读论文+Gap+工具的扁平索引 | 每次对话开始 |

### 核心资产（分析问题时读）

| 文件 | 作用 | 何时读 |
|:-----|:-----|:------|
| **papers/*.md** | 每篇论文的结构化卡片（贡献/方法/局限/关系） | 需要某篇论文的细节时 |
| **TAXONOMY.md** | 6维能力缺失 × 5种辅助模板 = idea生成矩阵 | 找发文方向时 |
| **meta/gaps.md** | 已验证的可做的空白方向 | 评估idea时 |
| **meta/cross-domain-ideas.md** | 跨领域假说验证记录 | 需要新颖视角时 |
| **meta/dead-ends.md** | 已验证无效的方向 | 避免踩坑时 |
| **meta/problem-map-report.md** | 完整的问题地图（2026-06-02生成） | 需要全景视角时 |

### 深度分析（按子方向深入时读）

| 文件 | 作用 |
|:-----|:-----|
| **topics/calibration.md** | 置信度校准方向的专题分析 |
| **topics/interaction.md** | 交互建模方向的专题分析 |
| **topics/physical-feasibility.md** | 物理可行性方向的专题分析 |
| **topics/planning-compatibility.md** | 规划兼容性方向的专题分析 |
| **topics/self-supervised.md** | 自监督预训练方向的专题分析 |
| **topics/representation-learning.md** | 表示学习方向的专题分析 |
| **topics/HiVT-architecture-line.md** | HiVT 架构演进线的追踪 |

### 工具层

| 文件 | 作用 | 调用方式 |
|:-----|:-----|:---------|
| **tools/paper_analyzer.py** | 方法论分类 + Gap匹配 + 卡片模板生成 | `python tools/paper_analyzer.py` 或 import |
| **tools/cross_validator.py** | 新论文 vs 已有论文的交叉验证 | `python tools/cross_validator.py --file test.json` |

### 元数据

| 文件 | 作用 |
|:-----|:-----|
| **meta/evolution-log.md** | 工具链和架构的进化日志 |
| **meta/research-assistant-mode.md** | 我的行为规范（纠错/反驳/分层记忆） |

---

## 四、我 vs 工具的边界

### 我负责（理解性工作，不可工具化）

| 能力 | 说明 | 输入 → 输出 |
|:-----|:-----|:-----------|
| 论文阅读理解 | 读懂论文的贡献、方法和局限 | 论文PDF/摘要 → 论文卡片 |
| Gap 发现 | 从论文的 limitations 和交叉对比中找空白 | 多篇论文 → 新Gap |
| 假说生成 | 从跨领域知识或逻辑推理中产生新想法 | Gap + 知识 → 假说 |
| 方向评估 | 判断一个方向的风险/收益/可论文化 | 假说 + 知识 → 推荐排序 |
| 故事构建 | 为论文设计叙事框架 | Gap + 方法 → 叙事模板 |
| 纠错反驳 | 交叉验证用户的判断与已有证据 | 用户输入 + 知识库 → 纠正 |
| 工具调度 | 决定什么时候调用什么工具 | 任务类型 → 工具调用 |

### 工具负责（机械性工作，必须用代码固化）

| 工具 | 能力 | 为什么不能靠我 |
|:-----|:-----|:--------------|
| paper_analyzer.py | 方法论分类、Gap关键词匹配 | 每次手工分类不稳定 |
| cross_validator.py | 跨论文矛盾/互补检测 | 12×N次对比，手工易遗漏 |
| （待建）consistency_checker.py | 检查 INDEX vs papers 一致性 | 文件多了必然不一致 |
| （待建）venue_matcher.py | 根据方法论类型推荐会议 | 规则明确，不需要推理 |

---

## 五、工具使用规范

### 当读了一篇新论文时

```
1. 运行 paper_analyzer.py（分类方法论 + Gap匹配）
2. 运行 cross_validator.py（交叉验证）
3. 我看工具输出 → 写论文卡片
4. 如果发现新Gap → 更新 meta/gaps.md
5. 更新 KNOWLEDGE_INDEX.md
```

### 当评估一个idea时

```
1. 查 KNOWLEDGE_INDEX.md → 确认是否有相关论文
2. 查 meta/gaps.md → 确认是否命中已有Gap
3. 查 TAXONOMY.md → 确认在矩阵中的位置（维度 × 模板）
4. 我的判断：新颖度、风险、可论文化
```

### 当跨论文对比时

```
1. 运行 cross_validator.py
2. 我解读输出 → 判断是真矛盾还是表面冲突
3. 如果有意外发现 → 记录到 evolution-log
```

---

## 六、知识库维护规则

### 必须做的

- ✅ 新论文读完 → 写卡片 + 更新 INDEX
- ✅ 新 Gap 发现 → 更新 gaps.md + INDEX
- ✅ 工具改进 → 更新 evolution-log.md
- ✅ 架构变化 → 更新本文档

### 不必做的

- ❌ 闲聊内容不记录
- ❌ 临时中间结果不记录
- ❌ 工具输出不重复存储（跑一下就有）
- ❌ 已有内容不冗余更新

---

## 七、当前架构状态

| 组件 | 状态 | 备注 |
|:-----|:----:|:-----|
| KNOWLEDGE_INDEX.md | ✅ | 12篇论文 + 5个Gap的索引 |
| papers/*.md | ✅ | 12篇论文卡片（部分待补全文） |
| TAXONOMY.md | ✅ | 6维×5模板矩阵 |
| meta/gaps.md | ✅ | 5个Gap，含A-E的详细分析 |
| meta/cross-domain-ideas.md | ✅ | 3个跨领域假说 |
| tools/paper_analyzer.py | ✅ | v1.0，6种方法论类型 |
| tools/cross_validator.py | ✅ | v1.0，5维交叉验证 |
| topics/*.md | 🔶 | 7个方向文件，深度参差不齐 |
| meta/dead-ends.md | 🔶 | 框架完整，暂无记录 |
| 一致性检查器 | 🕳️ 未建 | 待建 |
| 实验设计生成器 | 🕳️ 未建 | 暂不建（用户指示） |
