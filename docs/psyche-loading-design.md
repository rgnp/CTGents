# Psyche 加载与使用设计

> 状态：v1.2 已实现（状态机 + Psyche-owned Skills + 内容预算、冲突与组合契约）
> 范围：Psyche 的发现、加载、使用、Skill 调用、卸载、持久化与验证
> 核心定位：Psyche 是 CTGents 的主认知控制层；Skill 是 Psyche 按需调用的执行协议

## 一、目标与边界

目标不是“看到关键词就自动塞提示词”，而是让 Agent 在判断能力不足时，能可靠地装载正确认知框架，并在该框架下选择 Skill、使用工具、完成自检。

目标调用链固定为：

```text
用户目标 / 当前任务
  → General Kernel（常驻认知内核）
  → Psyche Stack（判断框架栈）
  → Skill（Psyche 选择的任务执行协议）
  → Tools + Knowledge（原子操作与证据）
```

不允许反向依赖：

- Skill 不负责发现或加载 Psyche。
- Tool 不承载认知判断。
- Knowledge 不直接变成行为指令。
- 关键词不直接触发领域 Psyche。

## 二、四层职责

### 1. General Kernel

每个会话常驻、不可卸载，负责所有 Psyche 都不能覆盖的底线：

- 事实与推测分层
- 不伪造证据
- 用户目标和明确指令优先
- 不越权、不把选择甩回用户
- 判断深度门禁
- 发现认知缺口后再提议加载 Psyche

General 中可被领域 Psyche 覆盖的是“默认判断方式”，不可被覆盖的是上述 kernel invariants。

### 2. Psyche

Psyche 回答“我应该按什么标准判断”。它拥有：

- 认知姿态
- 判断准则
- Competency Questions
- 负面知识
- 领域边界与已知盲区
- 可调用的 Skill 清单
- 可查询的 Knowledge 根

Psyche core 不应再复制完整操作流水线；具体步骤、模板、轴匹配和工具顺序下沉到 Skill。

### 3. Skill

Skill 回答“这次任务具体怎么做”。它只能在以下两种情况下激活：

1. 当前已激活的 Psyche 在 manifest 中声明可以调用它；
2. 用户明确要求使用该 Skill；若 owner Psyche 尚未激活，编排层先完成独立的 Psyche 加载事务，再激活 Skill。

Skill 可以声明工作流、参数轴、输出协议、所需工具组和按需参考资料，但 Skill 自身不能加载或卸载 Psyche。

### 4. Tools 与 Knowledge

- Tool：原子能力，如读文件、搜索、RAG、执行命令。
- Knowledge：判断证据和领域材料，按需检索，不常驻灌入。

Skill 组织 Tool；Psyche 决定使用哪个 Skill 和如何评价产出。

## 三、Psyche Manifest

每个可加载 Psyche 增加稳定清单：

```text
psyche/<id>/manifest.yaml
psyche/<id>/核心/<id>-core.md
```

子 Psyche 仍可保留目录层级，但依赖关系不再从路径猜测，完全以 manifest 为准。

v1 最小 schema：

```yaml
id: paper-deep-read
version: "0.3"
kind: domain                 # base | domain | subdomain | mode
core: 核心/paper-deep-read-core.md

requires:
  - research                 # 加载前必须存在的依赖

summary: 单篇论文的论证审计与分析
judgment_delta:
  - 从摘要层推进到 Claim/Grounds/Warrant/Qualifier 审计
  - 审计研究设计、领域位置和未声明假设

skills:
  - paper-deep-read
scope_default: task          # base | session | task
exit_checks:
  - 核心主张、证据和 warrant 是否逐一对应？
```

清单字段必须机械校验：id 唯一、core 存在、依赖存在、依赖图无环、Skill 引用存在、版本可解析。

## 四、发现机制：判断缺口，不做关键词触发

General 的发现流程保持四步：

1. 先用当前 Psyche Stack 推理。
2. 明确指出当前判断缺少的维度。
3. 查询 Psyche Catalog，寻找能补上该维度的 Psyche。
4. 说明“加载后会改变什么判断”；说不清则不加载。

有效提议示例：

```text
当前我只能复述论文贡献，但无法判断实验能否支撑核心 claim。
paper-deep-read 会增加 warrant、qualifier 和研究设计审计三个维度。
```

无效提议：

```text
用户提到了论文，所以加载 paper-deep-read。
```

Psyche Catalog 只暴露 `id + summary + judgment_delta`，不把所有 core 常驻上下文。Agent 发现缺口时调用 `psyche_catalog` 查询。

## 五、加载入口与授权

加载来源分三类：

### 1. Base

`general` 在新会话首轮自动加载，不可卸载。

### 2. 用户明确加载

用户执行 `/psyche load <id>` 或明确说“加载 X Psyche”时立即加载，不再二次确认。

### 3. Agent 自主加载

Agent 只有在能结构化说明认知缺口时，才可自主加载，并且固定为低摩擦、可自动回收的 `task` scope：

```text
psyche_catalog(query)
  → 确认目标 Psyche 能补足缺失判断维度
  → load_psyche(id, reason=cognitive_gap, scope=task)
  → 任务完成或归档时自动停用
```

Agent 不得自主创建 `session` 常驻状态；`session` scope 只由用户明确加载。依赖 Psyche 随目标自动加载，不逐层重复询问。例如加载 `learning-method` 后，事务自动解析：

```text
general → software-development → psyche-building → learning-method
```

模型工具 `load_psyche` 必须带 `reason`；仅提到关键词、文件名或主题不构成有效理由。

## 六、原子加载事务

一次加载必须是全有或全无：

1. 从 catalog 解析目标 manifest。
2. 构建完整依赖 DAG。
3. 拓扑排序，生成加载顺序。
4. 预读并校验所有 core、版本、Skill 引用和冲突。
5. 任一步失败则不写 `ctx.log`。
6. 全部通过后，按依赖顺序 append 激活事件。

激活事件结构：

```json
{
  "role": "system",
  "content": "【Psyche activated: paper-deep-read】...core...",
  "_psyche_event": {
    "type": "activate",
    "id": "paper-deep-read",
    "version": "0.3",
    "content_hash": "...",
    "scope": "task",
    "source": "user|agent|dependency|base",
    "reason": "缺少 warrant 审计",
    "activation_id": "..."
  }
}
```

Psyche 必须在工具结果完整写入后再 append，继续遵守 assistant/tool 紧邻协议。

## 七、Active Psyche Stack

运行时不再通过“log 里出现过 `_psyche_meta`”推断当前状态，而是从 activate/deactivate 事件归约出 Active Stack。

顺序和优先级：

1. General Kernel 与系统安全规则
2. 用户当前明确指令
3. 更具体的子 Psyche
4. 父/领域 Psyche
5. General 的可覆盖默认准则

冲突规则：

- 子 Psyche 可以覆盖父 Psyche 的领域默认判断。
- 任何 Psyche 都不能覆盖 General Kernel。
- 两个无继承关系的 sibling Psyche 若声明冲突，加载事务拒绝，要求明确选择或声明组合策略。
- 后加载不自动等于高优先级，优先级来自 manifest 关系。

## 八、加载后的使用闭环

加载不是“把文本塞进去就完成”，必须触发一次认知重算：

```text
Activate
  → 重新审视当前问题
  → 用 competency_questions 找此前遗漏
  → Psyche 决定是否调用 Skill
  → Skill 执行
  → Psyche 按判断准则审查结果
  → 回复前跑 exit check
```

每个 Psyche 的通用使用契约：

1. **Re-evaluate**：加载后重新判断当前问题，不能沿用加载前的浅层结论。
2. **Select Skill**：需要流程化执行时，从本 Psyche 声明的 Skill 中选择。
3. **Ground**：需要事实支撑时查询 knowledge roots，不把 core 当事实库。
4. **Exit Check**：最终回复前只检查本次相关的 competency questions，不做空泛“是否遵循 Psyche”提醒。

现有每轮通用自律提示应改成按 active Psyche 派生的具体 exit checks，且仅在 Stack 变化时追加一次。

## 九、Psyche 调用 Skill

调用方向固定为：

```text
active Psyche
  → activate_skill(skill_id, axes, reason)
  → Skill loader 校验 owner Psyche 已激活
  → 加载 core workflow + 命中 fragments
  → 按需启用工具组
  → 执行完成后结束 Skill scope
```

例如：

```text
paper-deep-read Psyche 已激活
  → 判断本次需要论文结构化深读
  → 调用 paper-deep-read Skill
  → depth=normal, paper_type=research, domain=autonomous-driving
  → Skill 加载对应 workflow/fragments 和 research 工具组
```

`skills/paper-deep-read/SKILL.md` 已删除反向加载步骤；运行时会在 owner Psyche 未激活时机械拒绝。

## 十、生命周期与卸载

Psyche 只支持三种 scope：

- `base`：General，整个会话有效，不可卸载。
- `session`：领域长期背景，直到会话结束或用户显式卸载。
- `task`：当前任务有效；`task_done` 或任务归档后自动停用，`need_user` 只是暂停，不停用。

卸载必须 append deactivate 事件，禁止删除历史 core 消息：

```json
{
  "role": "system",
  "content": "【Psyche deactivated: paper-deep-read】",
  "_psyche_event": {
    "type": "deactivate",
    "id": "paper-deep-read",
    "activation_id": "..."
  }
}
```

这样保持 append-only 和缓存稳定。上下文压缩时：

- active Psyche 的 core 必须保留；
- inactive Psyche 的旧 core 可被摘要或驱逐；
- session reload 通过事件流恢复相同 Active Stack。

## 十一、命令与工具接口

建议最终接口：

```text
/psyche list                    # catalog + active/pending 状态
/psyche status                  # 当前 Stack、依赖、scope、来源
/psyche load <id> [scope]       # 用户明确加载
/psyche unload <id>             # append deactivate；general 拒绝

psyche_catalog(query?)          # 模型查询判断能力目录
activate_skill(id, axes, reason)# Active Psyche 调用 Skill
```

## 十二、v1 已修复项

1. 依赖已由 manifest DAG 解析，`learning-method` 不再从目录猜父级。
2. `casual-chat` 和任意深度 Psyche 均由递归 Catalog 发现。
3. `general` 不可卸载；停用改成 append-only 事件。
4. `ctx.psyche_stack` 由事件流归约，可在会话恢复时重建。
5. 激活记录版本和 core hash；Stack 变化时刷新 snapshot。
6. 自律检查已由 active Psyche 的具体 `exit_checks` 派生。
7. `task_done` 和任务归档会自动停用 task-scope Psyche。

v1.2 已加入 Skill Catalog、轴校验、`activate_skill`、owner Psyche 强校验和 append-only Skill 生命周期；论文、Psyche 构建、界面审查和测试流程已下沉为 Skills。重型 core 已建立单体与依赖栈预算，时变路线从 autonomous-driving core 外置到 Knowledge。

## 十三、验收标准

必须用测试锁住以下行为：

- 新会话只自动加载 `general`。
- 关键词不会直接加载领域 Psyche。
- 加载 `paper-deep-read` 自动得到 `general → research → paper-deep-read`。
- 加载 `learning-method` 自动得到完整四级依赖链。
- `casual-chat` 可以通过 manifest 正常发现。
- 依赖缺失、循环、core 缺失、Skill 缺失时事务零写入。
- 重复加载幂等，不产生重复 core。
- `general` 无法卸载。
- 卸载只 append 事件，不删除历史消息。
- 切换/恢复会话后 Active Stack 一致。
- active Psyche 经过压缩仍保留，inactive Psyche 可被驱逐。
- Skill 未声明 owner 或 owner 未激活时拒绝执行。
- Psyche 加载事件永远位于完整 tool 结果之后，不破坏协议配对。

## 十四、实施顺序

### Phase 1：目录和依赖真相源（已完成）

- 增加 manifest schema、catalog scanner、validator、DAG resolver。
- 为现有 Psyche 补 manifest。
- 先不改变现有上下文注入格式。

### Phase 2：激活状态机（已完成）

- `_psyche_meta` 迁移为 activate/deactivate events。
- Active Stack、原子加载、append-only 卸载、session resync。
- 修复 general、casual-chat、二级依赖和 list。

### Phase 3：使用闭环（已完成）

- General 判断缺口协议接 `psyche_catalog`，Agent 自载固定为 task scope。
- 加载后 re-evaluate。
- 通用自律提示替换为具体 exit checks。

### Phase 4：Psyche → Skill

- [x] 增加 Skill owner 校验与 `activate_skill`。
- [x] 修正 paper-deep-read 的依赖方向。
- [x] 从 paper-walkthrough core 移除操作流程，保留判断框架。
- [x] 蒸馏 research、paper-deep-read、psyche-building、aesthetic-design、software-development、testing、tui-aesthetics 和 autonomous-driving。
- [x] 增加 `build-psyche`、`review-interface`、`design-tests` Skills。
- [x] 增加 core/依赖栈预算、冲突契约和行为契约测试。

Phase 1 和 Phase 2 是加载正确性的地基；完成后再接 Skill，避免在不可靠的 Psyche 状态上继续叠机制。
