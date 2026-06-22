# Psyche Building — 索引

- **领域**: Psyche 构建（Meta-Framework for Building Quality Psyche）
- **父 Psyche**: software-development（子 Psyche）
- **用途**: 指导型 — 嵌入构建流程，帮助在每一步判断质量、知道"够深了"、避免踩坑
- **版本**: 0.3（更新: 新增CommonKADS+LLM-era KE+一手来源验证）
- **最后更新**: 2026-06-22
- **覆盖精度**: 🟢全景拓扑 🟡因果结构 🟡判断准则 🟡负面知识（构建中）
- **核心文件**: psyche/software-development/sub/psyche-building/核心/psyche-building-core.md
- **阅读边界**: psyche/software-development/sub/psyche-building/阅读索引/reading-gaps.md
- **构建协议**: psyche/工具/构建协议.md

## 覆盖范围

三个核心模块（均匀深度覆盖）:
1. **质量标准** — 好 Psyche 的本质维度、量化评估方法
2. **构建方法论** — 每一步怎么做才质量高、怎么形成判断力、怎么蒸馏
3. **评估演化** — 自检到位的方法、更新/重构信号、退化检测

## 外部理论基础

| 来源 | 领域 | 应用 |
|------|------|------|
| OQuaRE (ISO/IEC 25000 SQuaRE) | 本体论质量评估 | 质量维度框架参考 |
| Ontology Development 101 (Noy & McGuinness) | 本体论构建方法论 | 迭代构建流程 + Competency Questions |
| **CommonKADS (Schreiber et al. 2000)** | **知识工程经典方法论** | **六模型套件 + 三层知识模型 + 全生命周期视角** |
| Gangemi (2005/2006) + Gomez-Perez (1995/2001) | 本体论评估 | 三维+四维评估标准，内容评估+结构评估 |
| **LLM-era KE (2025-2026)** | **最新知识工程实践** | **五种LLM构建本体方法 + GraphRAG + Agentic协作** |
| Knowledge Engineering Reference Architecture (2404.03624) | 知识工程标准化 | 工程实践结构化参考 |

## 加载指引

1. 前提：必须先加载父 Psyche `software-development`
2. 读本 Psyche 核心 — 注入认知框架
3. 读阅读索引/reading-gaps.md — 了解边界
4. pin("Psyche: psyche-building v0.1, 质量标准+构建方法论+评估演化", durable=true)
