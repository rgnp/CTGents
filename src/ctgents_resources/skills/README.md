# Skills 系统

Skill 是 Psyche 按需调用的执行协议层。

```text
用户目标
  → Active Psyche 判断缺口与质量标准
  → activate_skill(name, axes, reason)
  → 运行时校验 owner Psyche
  → 加载 SKILL.md、always_load 和命中 fragments
  → 按需读取 references
```

## 职责边界

- Psyche：决定怎么判断、何时需要流程、如何评价结果。
- Skill：定义具体步骤、轴、状态协议和输出契约。
- Tool：执行原子操作。
- Knowledge：提供事实、论文和时变证据。

Skill 不按关键词自行触发，不加载或卸载 Psyche，也不扩大用户授权。owner 关系只在 Psyche manifest 的 `skills` 字段声明；owner 未激活时，运行时拒绝 Skill 激活。

## 目录约定

```text
skills/<name>/
  SKILL.md                # 精简的核心流程
  manifest.yaml           # 版本、轴、always_load、on_demand
  static/core/            # 每次激活需要的稳定执行片段（可选）
  static/fragments/       # 按轴命中的片段（可选）
  references/             # 仅在明确场景下读取的大块协议（可选）
```

Manifest 的轴必须声明合法值和默认值。约定路径 `static/fragments/<axis>/<value>.md` 存在时由 loader 自动装配；不存在表示该轴只影响 SKILL.md 内的流程选择。

## 当前 Skills

| Skill | Owner Psyche | 用途 |
|---|---|---|
| `paper-deep-read` | `paper-deep-read` | 单篇论文结构化深读与论证审计 |
| `paper-walkthrough` | `paper-walkthrough` | 渐进带读、暂停与跨会话恢复 |
| `paper-extract` | `paper-deep-read` | 单篇论文全面资产拆解（评→取→用） |
| `paper-pipeline` | `paper-collection` | 论文入库自动化流水线 |
| `co-read` | `paper-co-read` | 对等共读——双方独立推演后碰撞 |
| `ad-method-audit` | `autonomous-driving` | AD 方法六不动点审计 |
| `contradiction-miner` | `research` | 文献矛盾挖掘——穷举搜索→深读→矛盾清单 |
| `gate-check` | `general` | 任务交付前门禁——逐条验证 gate 条件 |
| `build-psyche` | `psyche-building` | 新建、蒸馏、重构和评估 Psyche |
| `review-interface` | `aesthetic-design` | 界面视觉、状态与交互时间线审查 |
| `design-tests` | `testing` | 风险导向的测试设计、审查和失败定位 |
