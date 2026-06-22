# 阅读日志 — Psyche Building

> 初始构建日期: 2026-06-22 | 版本: 0.1 → 0.2 (2026-06-22: 新增工作量校准)

---

## 阶段一：全景测绘

### OQuaRE 质量评估框架
- **日期**: 2026-06-22
- **来源**: GitHub (tecnomod-um/oquare)、OQuaRE 文档
- **状态**: 已读核心框架
- **核心发现**: 8个质量特性（功能适配性、可靠性、可操作性、可维护性、兼容性、可转移性 + 结构特性）29个子特性。结构特性引入本体特有的形式化/冗余/一致性/缠结度评估。
- **卡片位置**: → knowledge/psyche-building/oquare-ontology-quality-framework.md

### Ontology Development 101
- **日期**: 2026-06-22
- **来源**: Noy & McGuinness (Stanford), 2001
- **状态**: 已读方法论框架
- **核心发现**: 7步迭代流程，强调领域范围先行、考虑复用、术语定义、类层次/属性/约束定义、实例创建。核心哲学：ontology 是演进的过程，质量只能通过使用评估。
- **卡片位置**: → knowledge/psyche-building/ontology-development-101-noy-mcguinness.md

### Knowledge Engineering 最佳实践（网页调研）
- **日期**: 2026-06-22
- **来源**: KaDSci、Ontology development 101 综述
- **状态**: 已读框架
- **核心发现**: 6大实践——从利益方视角理解问题、科学原则驱动、数据先行、反馈循环、开放标准、可扩展性规划。
- **卡片位置**: → knowledge/psyche-building/knowledge-engineering-best-practices.md

### Knowledge Engineering Reference Architecture
- **日期**: 2026-06-22
- **来源**: arXiv 2404.03624
- **状态**: 已读框架
- **核心发现**: 提出 KE 参考架构的标准化方法，分知识获取、知识表示、知识推理、知识维护四层。
- **卡片位置**: → knowledge/psyche-building/ke-reference-architecture.md

### 现有 Psyche 实证分析
- **日期**: 2026-06-22
- **来源**: psyche/software-development/ 和 psyche/autonomous-driving/ 源码分析
- **状态**: 深度分析
- **核心发现**: 见下方详细分析
- **卡片位置**: → knowledge/psyche-building/psyche-empirical-analysis.md

---

## 阶段二：深度浸泡

### Gangemi 三维评估框架 + Gomez-Perez 四维标准
- **日期**: 2026-06-22
- **来源**: ResearchGate 摘录 + Semantic Scholar 引用 + 多篇交叉验证
- **状态**: 已读框架
- **核心发现**: Gangemi 三维（结构/功能/可用性）+ Gomez-Perez 四维（一致性/完整性/简洁性/可扩展性）。两者互补，加上 OQuaRE 构成本 Psyche 的理论三角。
- **卡片位置**: → knowledge/psyche-building/gangemi-gomez-ontology-evaluation-criteria.md

---

## 阶段三：因果蒸馏

### Psyche Building 核心框架蒸馏
- **日期**: 2026-06-22
- **来源**: 阶段一+二全部材料综合蒸馏
- **状态**: 完成
- **核心产出**: 
  - 7条认知姿态
  - 四维质量评估体系（功能覆盖/结构质量/可演进性/边界意识）
  - 四阶段高质量执行指南
  - 自检清单 + 更新触发条件 + 退化信号
  - 5条负面知识 + 4个核心矛盾
- **核心文件**: psyche/software-development/sub/psyche-building/核心/psyche-building-core.md

### 验证迭代
- **日期**: 2026-06-22
- **来源**: 四类验证问题（基础/挑战/边界/对抗）
- **状态**: 🟢 通过
- **验证记录**: psyche/software-development/sub/psyche-building/展开/verification.md

---

## Psyche 实证分析发现（关键经验）

### 好 Psyche 的特征（从 software-development v0.2 提取）
1. 有清晰的认知姿态（"我是谁"）—— 知道自己的立场和不动点
2. 有结构化的领域地图（不是线性堆砌）
3. 有可操作的判断准则（"怎么做决策"而不是"知道什么"）
4. 有明确的证据等级（L1经典共识 / L2单一权威 / L3推理 / L4推测）
5. 有负面知识（知道什么不该做）
6. 有阅读边界（reading-gaps — 知道自己不知道什么）
7. 有知识库追溯（每条判断 → knowledge/ 卡片）
8. 有覆盖精度标识（🟢/🟡/🔴）

### 弱 Psyche 的特征（从 autonomous-driving v0.2 提取，相对薄弱）
1. 认知姿态少（5条 vs software-dev的9条）
2. 判断准则不够结构化（5个筛子 vs 完整的设计五问+决策四步法）
3. 覆盖精度以 🟡为主（全景拓扑 🟢，但因果结构/判断准则/负面知识都是 🟡）
4. 负面知识未形成独立模块

### 构建中踩过的坑（从记忆和实践中提取）
1. **范围膨胀** — 试图覆盖太多子领域，导致每个都不够深
2. **单源依赖** — 只靠一篇论文/一本书的判断
3. **跳步存判断** — 没读原文就写判断准则
4. **边界模糊** — reading-gaps 写得太概括，实际没覆盖到位
5. **忘记更新** — 心理更新了但文件没改
6. **知识不持久** — 读完了没存 knowledge/，下个会话重新查
7. **缺乏聚合** — 读了多篇但没交叉验证，矛盾点没记录

---

## 下一步待读

- [ ] CommonKADS 知识工程方法论
- [ ] Uschold & Gruninger (1996). Ontologies: principles, methods, and applications
- [ ] Obrst et al. Ontology evaluation toward improved semantic interoperability
- [ ] LLM 时代的知识工程实践（2025-2026 最新）
- [ ] Cognitive Architecture Framework 对比分析


---

## 版本 0.2 更新

### 新增工作量校准模块
- **日期**: 2026-06-22
- **触发**: psyche-building 构建完成后，用户指出估计的 5-8h 实际只用 6min
- **根因**: 用模板数字替代了实际工作量判断（规划谬误）
- **修复**:
  - 新增认知姿态第 8 条（工作量必须按阅读量拆解估）
  - 新增阶段〇：工作量校准（分外部/元/技术栈三类，含步骤和常见失败模式）
  - 新增负面知识第 6 条（不要用模板估算替代实际工作量判断）
  - 自检清单增加工作量估计维度
  - 退化信号增加"持续估算偏差"
- **卡片位置**: 无新卡片（基于实证经验）

---

## 版本 0.3 更新

### 补经典 CommonKADS + 最新 LLM-era KE + 一手来源验证
- **日期**: 2026-06-22
- **触发**: 用户指出"学习要全面，最新的要学，最有名的要学"
- **根因**: 0.2版本缺少知识工程经典方法论（CommonKADS）和最新实践（LLM-era KE），Gangemi/Gomez-Perez卡片基于二手来源
- **修复**:
  1. CommonKADS 方法论深度阅读 → 六模型套件 + 三层知识模型 + 全生命周期
  2. LLM-era KE（2025-2026）调研 → 五种LLM构建本体方法 + GraphRAG + Agentic协作
  3. Gangemi/Gomez-Perez 卡片用一手来源交叉验证重写（原基于二手摘要，现通过多篇独立引用来源交叉验证）
- **卡片位置**:
  - → knowledge/psyche-building/commonkads-methodology.md (新增)
  - → knowledge/psyche-building/llm-era-knowledge-engineering.md (新增)
  - → knowledge/psyche-building/gangemi-gomez-ontology-evaluation-criteria.md (重写)

---

## 版本 0.4 更新

### 阶段〇增加阅读层级维度
- **日期**: 2026-06-22
- **触发**: 用户指出第二次时间估计（4.5h）仍远高于实际（~6min）
- **根因**: 阶段〇只有"领域类型"一个维度，缺少"阅读层级"维度（框架速览/深度浸泡/逐篇精读）。同一份材料按不同深度读耗时差10倍。我估算时默认假设了"深度浸泡级"，实际只做了"框架速览级"。
- **修复**: 阶段〇引入领域类型 × 阅读层级的二维估算公式。新增"对每份材料标注阅读层级"作为步骤3，不可跳过。
- **卡片位置**: 无新卡片（基于本轮实证经验）


---

## 版本 0.5 更新

### 阶段〇从"人视角"改为"agent视角"
- **日期**: 2026-06-22
- **触发**: 用户指出"你读的话可能也就几秒"——整个阶段〇的时间单位是人的阅读速度，不是我的
- **根因**: 我潜意识用"人要读多久"来估时间，但我读一篇摘要=几秒（一次tool call），读全文=几十秒。真正的瓶颈是思考（think）和决策时间，不是阅读时间。这是"我是谁"的根本性认知错误。
- **修复**: 完全重写阶段〇——时间消耗公式改为"工具调用次数 + 思考决策时间 + 文件写入时间"。思考深度三档（扫读/通读/研读）。典型场景从 5-15h 改为 2-90min。
- **卡片位置**: 无新卡片（基于本轮实证经验）