# 系统架构

功能的准入、状态归属、闭环完成定义和冻结范围见
[`functional-evolution-boundaries.md`](functional-evolution-boundaries.md)。本文件描述当前接线，
该文档约束后续如何演化。

## 整体架构

源码与个人状态使用双根目录：核心根保存代码、测试和可发布资源；`CTG_WORKSPACE_DIR`
保存 memory、knowledge、sessions、tasks 和 stats。虚拟运行路径由 `src/paths.py`
统一解析，详见 [`workspace.md`](workspace.md)。

```
┌────────────────────────────────────────────────────────────┐
│  main.py — 入口 · TUI/行式 REPL · run_agent_turn 主干 · 收尾  │
│    run_agent_turn = _drive_turn（对话）                       │
│                   + 任务自主续跑（task_loop）                  │
├────────────────────────────────────────────────────────────┤
│  llm.py — 对话循环 run_conversation · 工具循环 · 流式/重试    │
│    DeepSeekBackend（单 Pro 模型）· 缓存统计 · 滑窗压缩        │
│    SAFE 并行 + eager 预执行 · stormBreaker 死循环打破         │
├────────────────────────────────────────────────────────────┤
│  cache_context.py — 三段式上下文（prefix/log/scratch）       │
│    send() 纯追加（append-only）· 前缀哈希锁死 · 配对修复      │
├────────────────────────────────────────────────────────────┤
│  tools/ — file exec git project lint web code memory think   │
│    rag research paper storm learn self · tool_guard 边界      │
├────────────────────────────────────────────────────────────┤
│  记忆        │  可信            │  任务         │  会话         │
│  memory      │  gate_audit      │  tasks        │  session     │
│  psyche      │  guard/tool_guard│  task_loop    │  status_bar  │
│              │                  │  gaps/tracker │  ui/tui      │
└────────────────────────────────────────────────────────────┘
```

## 核心模块

### main.py — 入口与主干
- `CacheContext` 三段式上下文（prefix/log/scratch）。
- `_make_prefix_msgs()` 构建冻结缓存前缀：AGENTS.md + 记忆索引（轴①）（会话开始建一次、
  哈希锁死）。
- `run_agent_turn` 是唯一主干，所有入口（行式 REPL / TUI）都走它：
  `_drive_turn` 跑一轮对话 → 若这轮真推进了 `current.md` 则 `run_task_continuation`
  自主驱动后续步骤。轮末追加式审计（`_run_post_turn_audits`）已于 2026-06-25 整体移除
  （判断型 nudge 一直弹、无明显效果，还拖累客观审计可信度）；现在轮末不跑任何审计。
- `_finalize_session` 收尾：落盘会话 → 会话摘要 → 跨会话状态更新。Dashboard 专属的
  reflection 快照链已于 2026-07-27 退役；Gap 直接消费 tracker 原始事件和异常检测。
  会话收尾的「收割」（lesson/用户画像/项目知识 LLM 重写）已于 2026-06-23 整体删除，
  记忆改为显式 remember 驱动。
- TUI（默认）/ 行式 REPL 兜底、Esc 打断监听、`/reload` 热加载、文件预读。

### llm.py — 对话循环
- `LLMBackend` 抽象 + `DeepSeekBackend`（**单 Pro 模型**——固定单模型养肥 DeepSeek 前缀缓存）。
- `run_conversation` 工具调用循环：流式优先、失败降级非流式、指数退避重试。
- 工具执行：连续 SAFE 工具并行（`_execute_tool_batch`）、流式期间 eager 预执行 SAFE 工具。
- `_handle_tool_results` 单一实现：解析 → 执行 → stormBreaker → 压缩 → 写 log。
- 上下文管理：超 65% 触发滑窗压缩（LLM 摘要驱旧）、超阈值熔断、单轮请求数熔断。
- 缓存统计/取证：每请求记 hit/miss + 结构指纹，供 `/context` 归因展示。

### cache_context.py — 三段式上下文
- **Immutable Prefix**：系统提示/记忆索引，会话级冻结、`send()` 时哈希校验。
- **Append-Only Log**：user/assistant/tool 只追加。
- **Volatile Scratch**：纯内存，不发 API。
- `send()` 纯追加（reasonix 式 canonical）：前缀在前、其后只追加 log 的非 volatile 消息，
  **永不挂尾**。`_repair_tool_pairing` 在此唯一咽喉补全中断留下的光杆 tool_calls，防 400 卡死。

### 可信审计（现状）
- `gate_audit`：仅会话首轮核对一次 HEAD 树是否在质量门通过记录里，挡 `--no-verify` 等绕门；
  不是轮末机制。
- `completion_audit`（谎报完成检测）已随 2026-06-25 的轮末审计整体移除一并删除，取证逻辑
  仅存在 git 历史里，需要时可单独捞回。
- `citation_audit.py`（编造引用检测）原来的轮末调用点没了，但 `_context_text` 被
  `delegate_gate.py` 复用（子代理出处闸的 haystack 判据），不是死代码。
- 轮末不再运行任何审计（`_run_post_turn_audits` 已整体移除：判断型 nudge 一直弹、无明显
  效果，还拖累客观审计可信度）。

### 安全边界
- `guard` / `tool_guard`：C3 限 cwd、C10 读后写、C14 目录、P1 禁 git add -A、P2 禁 force-push。
- `file.py`：不可变安全核拒写（`is_immutable`），核心业务可写但走 import 冒烟安全带。
- pre-commit：密钥扫描 + ruff（裸 except / 函数行数 / lint 零错，按已开规则）。pytest 不进提交门（太重），靠手动跑 / 评审兜底。

### 记忆
- `memory`（remember/recall/forget），显式调用驱动，不再有自动收割。
- 记忆索引会话开始注入缓存前缀（轴①"越用越懂你"）。
- 同名更新保留 `created` 并递增 `revision`；fingerprint 合并保留稳定身份。`contradicts`
  会让被替代项退出 recall、常驻上下文和 MEMORY 索引。`forget` 默认移入
  `memory/_retired/` 可恢复归档，不物理销毁。
- `memory_audit` 只读报告陈旧、冲突、重复、停用和已归档候选；清理决定仍需显式
  `remember`/`forget`，不恢复自动 LLM 收割。`recall` 只记录实际返回项，`adopt_asset`
  要求同一会话先检索且写明具体用途，避免把搜索命中伪装成实际采用。
- `lesson` / `experience` / `user_model` / `project_model` 等会话收尾 LLM 收割子系统已于
  2026-06-23 整体删除（文件不存在），记忆改为「用出来的」——agent 显式 remember 才写入。

### 知识库
- `knowledge/` Markdown 是唯一源事实，Research RAG 只是可重建索引，不拥有文档生命周期。
- 索引保存源文件集合签名；新增、修改、删除、清空或整个目录消失都会触发重建/清空，
  不再用“最新 mtime”漏掉删除事件。`knowledge/_retired/` 不参与索引。
- `knowledge_audit` 只读报告索引新鲜度、空短文档、精确重复和 registry 断链。
  更新继续使用文件编辑工具，退役优先移动到 `_retired/`；系统不自动删除原始研究资料。
- `tasks/asset-usage.jsonl` 保存 memory/knowledge 的 retrieved → adopted → outcome 事件；
  任务通过、失败或放弃会关联到采用事件。`feedback_asset` 只能对已有 outcome 的采用显式
  写入 helpful/misleading 和理由；同一判断幂等，反向判断作为纠正事件保留。
  outcome 是参与证据，不是因果有效性评分。
- 使用审计只读报告 misleading、至少 3 次独立检索但从未采用、已有结果尚未反馈三类候选，
  不自动改变召回权重，也不修改或删除原始资产。

### 任务
- `tasks`：`current.md`（唯一活跃）+ `pending/` + `archive/`，含目标锚点、gaps 报告。
- `task_loop`：agent 真推进 `current.md` 后自主续跑后续步骤，停由 agent 判断（标 [!] / 全 [x] / 不再推进）。
- 结构化验收：`current.md` 可声明 `## 验收`，用 `steps` / `file:` / `command:` 形成最小
  AcceptanceSpec。自动归档和 `task_done` 必须先通过验收；成功证据写入 `## 验收结果` 后随任务归档。
  旧任务没有验收区时保持原行为。
- 验证回执：同步 `run_command` 执行 pytest、`ruff check`、`git diff --check` 后，
  `verification_receipts.py` 保存真实退出码、输出尾部、运行环境和完整工作区指纹。任务验收优先复用
  24 小时内且指纹未变化的回执；代码继续变化后自动重跑，避免旧测试结果给新代码背书。
- 异步验证使用同一回执格式：`run_async` 或同步超时转后台时冻结启动指纹，`poll` 与
  `drain_finished_jobs` 竞争时只有一个完成者可写回执。完成时工作区必须仍与启动一致；
  失败退出写失败证据，超时被杀或执行期间代码变化不写回执。
- 结构化停止：任务可在 `## 停止条件` 声明 `budget:`、`stall:` 和带时区的 `deadline:`。
  `task_loop` 每一步重新解析，重规划后下一步立即生效。用户中断、`need_user`、阻塞标记和验收失败
  始终是不可关闭的系统硬停止。
- 可靠性 gap：`gaps.py` 用来源、类型和受影响文件生成稳定 ID，并把
  `discovered / accepted / rejected / deferred / fixed / verified` 生命周期写入
  `tasks/gap-ledger.json`。`/fix N` 会显式接受候选，`/gap` 可决策或标记修复；
  `/gap verify ID` 强制重跑原检测源，只有原信号消失才通过。检测源失败时保持 `fixed`，
  已验证信号复发时自动重开为 `accepted`。performance gap 还会绑定原始
  `tool + anomaly_type`：标记 `fixed` 时冻结修复前基线，之后至少观察 3 个真正使用过该工具且样本充分的
  新会话；窗口内零复发才进入 `verified`，未使用该工具的会话不算成功证据。gap 缓存绑定完整工作区指纹，
  不会因未提交修改继续复用旧扫描。
- 共享工作回执：`work_receipts.py` 不接管 Task、Heartbeat、Gap 或资产状态，只统一记录
  work_id、stage、证据哈希、工作区指纹、产物 SHA-256 和跨系统引用。
  Task 的 work_id 与认知资产 task_key 相同；任务文本中的 `gap <ID>` 形成只读链接。
  Heartbeat 每跳回执由出处闸和 frontier 真实推进共同决定，delivery 等待用户显式处置。
  详细合同见 [`work-lifecycle.md`](work-lifecycle.md)。

### 会话与界面
- `session`：JSON 持久化，前缀单独冻结存盘（跨重启复用同一前缀字节、保持服务端缓存热）。
- `tui`（Textual 全屏）/ 行式 REPL 兜底 / `ui` 展示层 / `status_bar` 状态栏 / `diagnostics` 诊断。

## 数据流：一次对话

```
用户输入 → main.run_agent_turn
  → _drive_turn → process_turn → llm.run_conversation
    → _invoke_llm_eager（流式 + SAFE 工具 eager 预执行）
    → LLM 返回 tool_calls → _handle_tool_results
        → 解析 → tool_guard 边界拦截 → _execute_tool_batch（SAFE 并行）
        → stormBreaker → 压缩 → 写 log
    → 循环直到 LLM 返回纯文本
  → 若推进了 current.md → run_task_continuation（自主驱动后续步骤）
  → 落盘会话 + 冻结前缀
```

## 工具模块规范
1. 定义 `TOOLS_XXX`（OpenAI function calling 格式）+ `execute()` 调度。
2. 在 `tools/__init__.py` 注册。
3. 在 `tools/_tool_meta.py` 标安全等级（`PARALLEL_SAFE` 决定能否并行）。
4. 跨模块新接线同步加缝测试（C16）。

## 关键设计决策
- **缓存优先 / append-only**：单 Pro 模型 + 纯追加 send()，永不挂尾（挂尾是缓存命中塌陷的根因）。
- **单一主干**：所有入口走 `run_agent_turn`，循环同源、drift 闭合。
- **可信靠审计不靠纪律**：completion/citation/gate 三审计供客观事实，判断仍交给 agent。
- **热加载**：`/reload` 刷新指令 + 工具，无需重启。
