# 阅读日志 — 软件开发 Psyche

> 初始构建日期: 2026-06-21 | 版本: 0.2

---

## 阶段一：全景测绘

### 业界工程能力矩阵调研
- **日期**: 2026-06-21
- **来源**: Stride开源矩阵、FullScale工程能力模型、国内软件工程师能力体系
- **状态**: 已读摘要+核心框架
- **核心发现**: 8大能力维度。psyche聚焦代码规范/架构/测试/演进/判断力5维。
- **卡片位置**: —（调研性阅读，非经典文献）

---

### Clean Code 原则（含批评性视角）
- **日期**: 2026-06-21
- **来源**: Codacy、qntm.org深度批评
- **状态**: 已读多篇
- **核心发现**: 原则可取，教条要避免。Martin自己的一些示例被批评为过度抽象。
- **卡片位置**: → knowledge/software-development/clean-code-martin-with-critique.md

---

### SOLID + 设计模式
- **日期**: 2026-06-21
- **来源**: DigitalOcean GoF、Clean Code Guy
- **状态**: 已读核心

---

### 测试策略（2026版）
- **日期**: 2026-06-21
- **来源**: Digital Applied、Google Testing Blog
- **状态**: 已读全文
- **核心发现**: 测试金字塔有效。diff coverage > 全局覆盖率。flaky test是核心问题。
- **卡片位置**: → knowledge/software-development/testing-strategy-2026.md

---

### Google 代码审查标准
- **日期**: 2026-06-21
- **来源**: Google eng-practices
- **状态**: 已读全文
- **卡片位置**: → knowledge/software-development/google-eng-practices-code-review.md

---

### 技术债务管理
- **日期**: 2026-06-21
- **来源**: Dockyard、vFunction
- **状态**: 已读

---

### Agentic 设计模式
- **日期**: 2026-06-21
- **来源**: Augment、Anthropic "Building Effective Agents"、Anthropic "Writing Tools for Agents"
- **状态**: 已读多篇全文
- **卡片位置**: → knowledge/agent-development/（详见 agent-development psyche）

---

### ADR（架构决策记录）
- **日期**: 2026-06-21
- **来源**: Martin Fowler、adr.github.io、Michael Nygard
- **状态**: 已读全文
- **核心发现**: ADR的价值不仅在于记录，更在于写作过程迫使你理清思路、暴露分歧。
- **卡片位置**: → knowledge/software-development/adr-architecture-decision-record.md

---

## 阶段二：深度浸泡（v0.2）

### A Philosophy of Software Design — Ousterhout
- **日期**: 2026-06-21
- **来源**: 多篇深度书评（pathsensitive.com, theleo.zone, smlx.dev）
- **状态**: 深度阅读，交叉验证多篇书评
- **卡片位置**: → knowledge/software-development/ousterhout-philosophy-of-software-design.md

**核心发现**:
- 复杂性 = 变更放大 + 认知负荷 + 不明显牵连
- 深层模块 > 浅层模块（接口比实现简单才是好模块）
- 设计两次（Design It Twice）：对重要模块至少设计两个完全不同的方案
- 注释先行：先写接口注释描述约定，再写实现
- TDD不保证好的设计——它只保证功能正确
- 配置参数是懒惰的接口设计

**对psyche的影响**: 增加了第1/2条认知姿态，重构了整个设计哲学

---

### The Pragmatic Programmer — Hunt/Thomas
- **日期**: 2026-06-21
- **来源**: 多篇深度书评（arkadiuszchmura.com）
- **状态**: 深度阅读
- **卡片位置**: → knowledge/software-development/pragmatic-programmer-hunt-thomas.md

**核心发现**:
- 曳光弹 ≠ 原型：原型会丢弃，曳光弹会留在最终产品中
- 破窗理论：不修复小问题→传递"没人管质量"的信号
- DRY的更深含义：不是代码复用，是知识不重复
- 知识组合：技术广度是职业生涯的保险
- 石头汤：展示部分成果来推动变革

**对psyche的影响**: 增加了第5/6条认知姿态

---

### Working Effectively with Legacy Code — Feathers
- **日期**: 2026-06-21
- **来源**: understandlegacycode.com
- **状态**: 深度阅读
- **卡片位置**: → knowledge/software-development/feathers-working-effectively-with-legacy-code.md

**核心发现**:
- 遗留代码 = 没有测试的代码
- 修改无测试代码的第一步：写表征测试（characterization test）
- Sprout技术：新方法写新代码，老代码加一行调用
- Wrap技术：用新类包住老代码
- 接缝（Seam）：不需要修改就能改变行为的地方——测试的入口

**对psyche的影响**: 增加了第4条认知姿态，以及安全修改流程

---

### Cynefin 框架
- **日期**: 2026-06-21
- **来源**: Wikipedia + 多篇文章交叉验证
- **状态**: 已理解核心框架
- **卡片位置**: → knowledge/software-development/cynefin-framework-snowden.md

**核心发现**: 4类问题需要4种不同解法。用错类型=用错方法。简单→Best Practice，繁杂→专家分析，复杂→探测-感知-响应，混沌→先稳定再诊断。

**对psyche的影响**: 增加了问题分类框架，防止用错误方法处理错误类型的问题

---

### 无责事后复盘文化
- **日期**: 2026-06-21
- **来源**: Google SRE、blameless postmortem实践
- **状态**: 已读核心

**核心发现**: 复盘转变成追责→下次没人说实话。5 Whys根因分析。输出可执行的改进项，不写空话。

---

### 估算方法论
- **日期**: 2026-06-21
- **来源**: 规划谬误研究、Reference Class Forecasting
- **状态**: 已读核心

**核心发现**: 规划谬误是系统性的。参考历史实际耗时比"觉得多久"准确。估算是一个概率分布，不是一个数字。

---

## 下一步待读

- [ ] OWASP Top 10 安全编码
- [ ] CI/CD 管道搭建实战
- [ ] 分布式系统设计要点
- [ ] Code Complete（McConnell）— 另一本经典
- [ ] Domain-Driven Design（Evans）— 战略设计
