# 测试 Psyche ↔ 知识库链接

> ⚠️ 每条判断标注来源。判断有 knowledge/ 卡片支撑的标 L2，无卡片支撑的标 L3/L4。

---

| Psyche 判断 | 来源（→ knowledge/ 卡片） | 证据等级 |
|------------|--------------------------|---------|
| FIRST 五维质量框架：Fast/Isolated/Repeatable/Self-validating/Timely | → knowledge/testing/first-principles.md | L2 |
| AAA 三要素：Arrange-Act-Assert，段间空行 | → knowledge/testing/aaa-pattern.md | L2 |
| Act 通常只有一行 | → knowledge/testing/aaa-pattern.md | L2 |
| 测行为不测实现 | → knowledge/testing/test-behavior-not-implementation.md | L2 |
| 风险导向：核心逻辑全面覆盖，getter/setter 不测 | → knowledge/testing/augment-testing-best-practices.md | L2 |
| 95% 覆盖率但无断言的测试比不覆盖更危险 | → knowledge/testing/augment-testing-best-practices.md | L2 |
| fixture scope 策略：session/module/function | → knowledge/testing/pytest-best-practices.md | L2 |
| parametrize 消除重复，用 ids 给名字 | → knowledge/testing/pytest-best-practices.md | L2 |
| 内置 fixture（tmp_path/monkeypatch/capsys）优先 | → knowledge/testing/pytest-best-practices.md | L2 |
| 倾向 stub 而不是 mock | → knowledge/testing/pytest-best-practices.md | L2 |
| 速度优化：先 profile 再动手 | → knowledge/testing/test-speed-optimization.md | L2 |
| session-scope fixture 降重复开销 | → knowledge/testing/test-speed-optimization.md | L2 |
| 过度 mock、测实现、共享可变状态是三大反模式 | → knowledge/testing/test-antipatterns.md | L2 |
| sleep 是 flaky 的万恶之源 | → knowledge/testing/test-antipatterns.md | L2 |
| 突变测试验证测试质量 | → knowledge/testing/augment-testing-best-practices.md（合并） | L3 |
