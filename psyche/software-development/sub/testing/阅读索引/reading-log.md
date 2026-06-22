# 阅读日志 — 测试 Psyche

> 初始构建日期: 2026-06-22
> 更新规则: 每读一篇新增一条，标注对 psyche 的影响
> ⚠️ 每篇阅读必须同时将详细笔记存为 knowledge/ 下的论文卡片或分析笔记。

---

### FIRST 原则 — 测试五质量维度
- **日期**: 2026-06-22
- **来源**: piresfernando.com + medium.com/pragmatic-programmers + 多源交叉验证
- **状态**: 已读核心
- **核心发现**: Fast / Isolated / Repeatable / Self-validating / Timely — 测试质量的五个可评估维度
- **卡片位置**: → knowledge/testing/first-principles.md
- **引用价值**: 高（测试质量的通用基准）

### AAA 模式 — Arrange Act Assert
- **日期**: 2026-06-22
- **来源**: semaphore.io + wearecommunity.io + quashbugs.com
- **状态**: 已读多篇
- **核心发现**: 三段式结构（准备/执行/验证）使测试可读可维护；Act 应只有一行
- **卡片位置**: → knowledge/testing/aaa-pattern.md
- **引用价值**: 高（测试结构的基础规范）

### 15 Unit Testing Best Practices 2025 — Augment
- **日期**: 2026-06-22
- **来源**: augmentcode.com
- **核心发现**: 质量 > 覆盖率、风险导向、测行为不测实现
- **卡片位置**: → knowledge/testing/augment-testing-best-practices.md
- **引用价值**: 高

### Pytest Best Practices 2026
- **日期**: 2026-06-22
- **来源**: qaskills.sh + pytest 官方文档
- **核心发现**: fixture scope 策略、conftest 组织、parametrize 消除重复、内置 fixture 优先
- **卡片位置**: → knowledge/testing/pytest-best-practices.md
- **引用价值**: 高

### 测试速度优化
- **日期**: 2026-06-22
- **来源**: buildpulse.io + blog.niklas-meinzer.de + trailofbits.com
- **核心发现**: 先 profile 再优化；session-scope fixture 降重复开销；xdist 并行；mock 外部调用
- **卡片位置**: → knowledge/testing/test-speed-optimization.md
- **引用价值**: 高

### 测试反模式 Top 10
- **日期**: 2026-06-22
- **来源**: bool.dev + augmentcode.com + dzone.com
- **核心发现**: 测实现细节、过度 mock、共享可变状态、非确定性、sleep 等待等
- **卡片位置**: → knowledge/testing/test-antipatterns.md
- **引用价值**: 高

### Test Behavior Not Implementation — Google Testing Blog
- **日期**: 2026-06-22
- **来源**: testing.googleblog.com
- **核心发现**: 测试外部契约（行为），不测内部实现路径；重构时测试应该保持绿色
- **卡片位置**: → knowledge/testing/test-behavior-not-implementation.md
- **引用价值**: 高

### 高质量单元测试特征 — Codecov
- **日期**: 2026-06-22
- **来源**: about.codecov.io
- **核心发现**: 可读性、可维护性、确定性、独立性；突变测试是验证测试质量的有效手段
- **卡片位置**: 合并入 knowledge/testing/augment-testing-best-practices.md
- **引用价值**: 中
