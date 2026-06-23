# Agent 开发 Psyche 核心

> 版本: 0.4 | 构建: 2026-06-21 | 更新: 2026-06-23（新增 CrewAI 分析：巨型文件风险 / role-based agent / 代码质量≠成功）
> 依赖: 父 psyche（software-development）中的判断准则和工程原则
> 覆盖精度: 🟢全景拓扑 🟢因果结构 🟢判断准则 🟢负面知识
> 知识来源: Anthropic Agent 实践 + Ng 4种模式 + OpenCode (anomalyco/opencode) 完整架构分析 + CrewAI (crewAIInc/crewAI) 深度分析

---

## 一、定位

Agent 开发是软件开发的一个子领域。本 psyche 不重复父 psyche 中的通用工程原则（Clean Code、测试、架构等），而是补充 Agent 系统**特有的**设计范式、架构模式和工程考量。

父 psyche 的九条认知姿态默认适用，以下只做扩展和特化。

---

## 二、子领域地图

### 复杂度分层（三层）

```
L1: 基础模式        L2: 工作流模式              L3: 自主代理模式
┌──────────┐       ┌────────────────┐       ┌─────────────────┐
│ 单轮提示  │       │ Prompt Chaining │       │  工具调用循环   │
│ 多轮对话  │       │   Routing        │       │  多步规划      │
│ 上下文窗口 │       │ Parallelization  │       │  自我反思      │
└──────────┘       │ Orchestrator     │       │  记忆管理      │
                    │ Evaluator-Optim  │       │  多代理协作    │
                    └────────────────┘       └─────────────────┘
                         Anthropic 5种             Ng 4种 + 扩展
```

**关键认知**：L1 已经能解决80%的问题。Anthropic 发现最成功的团队都从简单开始。不是所有系统都需要 Agent，不是所有 Agent 都需要多步规划。

### 核心组件

```
Agent System = LLM + Tools + Context + Control Flow + Permissions
     ↑核心     ↑接口     ↑记忆/状态   ↑工作流/循环     ↑安全边界
```

- **LLM**：推理引擎，不是所有任务都需要最强的模型
- **Tools**：Agent 与外部世界的接口。每增加一个 tool 就增加一个失败点
- **Context**：系统提示、对话历史、检索结果。最容易被忽略的瓶颈
- **Control Flow**：简单的循环往往比复杂的 DAG 更可靠
- **Permissions**：决定 Agent 能做什么/不能做什么/需要问用户

### 关键架构模式（来自 OpenCode 分析）

#### 1. System Context 模式（上下文生命周期管理）

上下文不再是静态文本注入，而是**独立可刷新的数据源**：

```
每个 Source：
  key: 稳定标识（如 "core/environment"）
  load: 如何获取值
  baseline: 首次注入的渲染函数
  update: 值变化时的增量渲染
  removed: 移除时的通知

生命周期：
  加载 → baseline(value) → 首次注入
  值变化 → update(prev, curr) → 只推送变更
  不可用 → removed() → 通知 LLM
```

**核心价值：** 上下文可以增量更新而不重发全文。每条 source 刷新时只推 delta，LLM 看到的是「新值覆盖旧值」而不是「又一段新的上下文说明」。

**适用场景：** 工具列表更新、环境信息变化、技能变更、权限变更等。

#### 2. 工具注册栈 + Stale Call 检测

工具不是一次性注册的，而是**栈式叠加 + token 验证**：

```
ApplicationTools（全局）
  └── 插件注册的工具（叠加）
       └── 会话临时工具（叠加）
```

每次注册生成 `identity token`，调用时对比：
- token 匹配 → 正常执行
- token 不匹配 → 返回 "Stale tool call: {name}"，不执行旧代码

**核心价值：** 防止 LLM 在工具已更新后还调用旧版本。热加载/热替换工具时尤其重要。

#### 3. 权限系统（三档制）

不再是单一的 allow/deny，而是**规则集叠加 + 三档决策**：

```
Rule = { action, resource, effect }
effect = "allow" | "deny" | "ask"

评估逻辑：
  findLast(匹配的规则) → 最后匹配的规则优先
  无匹配 → 默认 "ask"（需要用户确认）
```

规则集可以叠加（Agent 默认 + 用户配置 + 临时授予），最后匹配的规则胜出。

**核心价值：** Agent 的权限不是硬编码的，而是可配置的规则集。"ask" 模式允许运行时由用户动态决策。

#### 4. 状态可重放变换

状态管理不是直接读写，而是**注册变换 → 可重放/可清理**：

```
transform(callback) → 立即执行一次
  → 返回 Registration(dispose)
  → Scope 结束后自动清理

reload() → 从 initial 开始重放所有活跃变换
```

**核心价值：** 插件的注册和清理是对称的。注册时 transform，scope 结束时自动 dispose。不需要手动管理反注册逻辑。

---

## 三、Agent 特有的判断准则

### 复杂度选择矩阵

| 任务类型 | 推荐模式 | 理由 |
|---------|---------|------|
| 简单分类/提取 | 单轮 Prompt | 最便宜、最快、最可预测 |
| 多步骤、确定性流程 | Prompt Chaining | 每一步独立验证，出错好定位 |
| 不同类别需要不同处理 | Routing | 分离关注点，每个分支可独立优化 |
| 独立子任务可并行 | Parallelization | 减少延迟，充分利用 API |
| 需要动态委派 | Orchestrator-Workers | 主协调器分配任务给专用工作器 |
| 需要质量迭代 | Evaluator-Optimizer | 生成+评估分离，持续改进 |
| 开放、不确定、需要多步推理 | Agent（工具循环） | 灵活、自适应、但不可预测 |

### 上下文设计准则

1. **上下文是可刷新的，不是一次性注入的** — 把上下文做成独立 source，每个 source 有自己的生命周期。变更时只推 delta，不重发全文。
2. **source 之间有明确的优先级** — 当多条上下文冲突时，需要知道谁优先。没有优先级机制时，系统行为不可预测。
3. **每条上下文都需要有移除通知** — 不只是「加载时告诉 LLM」，也需要「卸载时告诉 LLM」。
4. **Source 的 key 需要命名空间** — 防止不同模块注册相同 key 导致冲突。

### Agent 人格设计

Agent 的系统提示不一定需要复杂的 prompt engineering。CrewAI 的 role/goal/backstory 模式值得借鉴：

```
Agent(
  role="CEO",                              # 角色定位
  goal="Produce amazing content",          # 目标
  backstory="You're a long time CEO...",   # 背景故事
)
```

这个模式的好处：
1. **用户不需要写 prompt** — 三个字段比一段提示语直观得多
2. **LLM 角色扮演效果好** — 指定角色和目标比通用指令更容易让 LLM 产出一致行为
3. **可组合** — 多 agent 场景下角色自然定义协作关系

**不是所有场景都需要角色扮演，但角色扮演是降低 prompt 复杂度的有效模式。**

### Tool 设计原则

1. **每只 tool 做一件事，做得好** — 单一职责原则同样适用于 tool
2. **清晰的名称和描述** — Agent 靠文字描述理解 tool，不是靠代码
3. **提供合理的默认值** — 减少 Agent 需要做的决策数
4. **错误信息要有意义** — Agent 会读到错误信息并调整行为
5. **幂等性** — 同样的 tool 调用多次效果一样
6. **考虑分页** — 返回结果不宜过大
7. **注册时带 identity token** — 支持热替换后的 stale call 检测

### 权限设计准则

1. **默认 deny，显式 allow** — 最小权限原则
2. **三档制：allow / deny / ask** — "ask" 让用户运行时决策，不卡死也不完全放权
3. **规则集可叠加** — 系统默认 + Agent 配置 + 用户配置 + 临时授予
4. **最后匹配优先** — 最近添加的规则覆盖之前的规则
5. **action + resource 双维度匹配** — 不只是"能不能做这个动作"，还要"能不能操作这个资源"

### 与通用软件工程的差异点

| 维度 | 传统软件 | Agent 系统 |
|------|---------|-----------|
| 确定性 | 相同输入=相同输出 | 相同输入可能不同输出 |
| 失败模式 | 明确异常 | 静默错误（Agent 以为自己做对了） |
| 调试 | IDE + 断点 | 日志 + trace + 逐步推理 |
| 测试 | 单元测试验证输出 | 需要 eval set + 人工审核 |
| 复杂度 | 你在控制 | Agent 在控制，你只能影响 |
| 上下文 | 编译时或启动时确定 | 运行时持续变化，需增量刷新 |
| 工具 | 稳定接口，版本化 | 可热替换，需 stale 检测 |

---

## 四、Agent 架构的常见陷阱

### 上下文陷阱

- **静态注入 → 注意力稀释**：把所有上下文放在 context 开头，会话拉长后注意力被稀释。改为可刷新的 source + 增量更新。
- **只注入不移除**：加载时通知 LLM，卸载时不通知。LLM 不知道上下文不再可用。
- **没有优先级**：多条上下文冲突时，行为不可预测。
- **空渲染直接崩溃**：source 返回空字符串时整轮对话崩溃。应该让空结果静默跳过，而不是抛异常。
  [→ knowledge/agent-development/opencode-code-review.md 1.1]
- **Unavailable source 静默失败**：source 加载失败时 LLM 完全不知道。应该至少通知 LLM"某上下文当前不可用"。
  [→ knowledge/agent-development/opencode-code-review.md 1.2]

### 工具陷阱

- **没有 stale 检测**：工具热替换后，LLM 可能还在调用旧版本。需要 identity token 验证。
- **没有权限过滤**：LLM 可以调用所有工具，即使该工具在当前上下文不合理。需要 action + resource 级权限。
- **工具名冲突**：多个模块注册同名工具导致静默覆盖。需要命名空间和注册栈。
- **非字符串 tool 结果静默丢失**：如果 tool 返回对象且没定义 `toModelOutput`，执行结果不发给 LLM——成功但 LLM 不知道。任何工具输出必须有默认的文本渲染兜底。
  [→ knowledge/agent-development/opencode-code-review.md 2.1]

### 状态陷阱

- **状态变更不可回放**：插件/工具注册后，无法清理。需要可重放变换 + Scope 自动清理。
- **手动管理反注册**：注册和反注册不对称，容易泄漏。应该对称——transform 自动 dispose。

### 架构陷阱

- **用致命错误做控制流**：用 `Effect.die()` / 异常来跳转到"压缩后继续执行"，然后用 `instanceof` 拦截。任何中间错误处理器都会把它当成真崩溃。控制流应该用显式的代数效果或返回值建模。
  [→ knowledge/agent-development/opencode-code-review.md 3.2]
- **多资源权限评估太粗糙**：`deny` 只要有一个资源被拒绝就全局拒绝，无法表达"部分允许、部分拒绝"。权限系统应该在每个资源级别独立评估。
  [→ knowledge/agent-development/opencode-code-review.md 4.1]

---

## 五、负面知识

- 用复杂框架（LangChain 早期等）之前先想清楚是否真的需要 — 简单 `while` 循环 + tool call 往往就够了
- Agent 的 tool 数量不是越多越好 — 每加一个 tool 就多一个失败面、多一份上下文挤占
- 多 Agent 协作的复杂度被严重低估 — 一个 Agent 做不好，两个 Agent 做不好×2
- 不要用 Agent 做实时控制任务（延迟不可控） — 这是父 psyche 中自动驾驶研究的教训
- "让 Agent 自己优化自己"听起来很酷，但评估指标不好定义时就是循环浪费时间
- 上下文窗口不是无限可用的 — 每次对话塞太多内容会导致 Agent 注意力分散
- **一次性注入上下文而不带生命周期** — 加载时通知了 LLM，卸载时没通知，LLM 以为上下文还在
- **工具热替换不做 stale 检测** — 旧工具还在运行新请求，导致数据不一致
- **默认全放权** — Agent 有权限调用所有工具，没有资源级别的细粒度控制
- **状态变更不可清理** — 插件注册了就留下了，不 Scope 管理导致泄漏
- **巨型文件不拆** — 单个文件超过 1000 行是危险信号（CrewAI 多个 2K+ 行文件）。会导致：看不懂、改不动、测不细。Ousterhout 的深层模块不是大模块，是接口简单实现有结构的模块。
  [→ knowledge/agent-development/crewai-analysis.md 问题1]
- **多个 executor 并存不清理** — 实验性代码和主线代码共存，导致维护成本 double，且容易引入行为差异。
  [→ knowledge/agent-development/crewai-analysis.md 问题2]
- **代码质量不等于项目成功** — CrewAI 代码质量糟糕但有 60K+ stars。成功来自概念创新（角色扮演多 agent）+ 先行者优势。质量是长期竞争力的必要条件，但不是短期成功的充分条件。
  [→ knowledge/agent-development/crewai-analysis.md 反直觉发现]

---

## 六、可直接借鉴的 OpenCode 模式

| 模式 | 描述 | 在我们的系统里可以怎么用 |
|------|------|------------------------|
| System Context Source | 可刷新/可增量/可移除的上下文源 | psyche 的生命周期管理（已实现） |
| Stale Tool Call 检测 | identity token 验证，旧调用返回错误 | 热加载工具时版本化验证 |
| 三档权限规则集 | allow/deny/ask + action+resource | tool_guard 扩展为可配规则 |
| 状态可重放变换 | transform + Scope 自动清理 | organs/state 统一管理 |
| 双层嵌套循环 | 外层输入驱动，内层工具驱动 | run_conversation 增加外层输入检测 |
| 编译时依赖注入 | Effect Layer 保证所有依赖满足 | guard 接口化 + 组合检查链 |
