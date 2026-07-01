# 系统架构

## 整体架构

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
- `_make_prefix_msgs()` 构建冻结缓存前缀：AGENTS.md + 记忆索引（轴①）+ 长期目标 ambitions
  + 被动进化反思 reflections（都 session 稳定，会话开始建一次、哈希锁死）。
- `run_agent_turn` 是唯一主干，所有入口（行式 REPL / TUI）都走它：
  `_drive_turn` 跑一轮对话 → 若这轮真推进了 `current.md` 则 `run_task_continuation`
  自主驱动后续步骤。轮末追加式审计（`_run_post_turn_audits`）已于 2026-06-25 整体移除
  （判断型 nudge 一直弹、无明显效果，还拖累客观审计可信度）；现在轮末不跑任何审计。
- `_finalize_session` 收尾：落盘会话 + 冻结前缀 → 写反思统计（`stats/*_reflection.json`，
  仅落盘供 dashboard 看，不再注入前缀）。会话收尾的「收割」（lesson/用户画像/项目知识 LLM
  重写）已于 2026-06-23 整体删除，记忆改为显式 remember 驱动。
- TUI（默认）/ 行式 REPL 兜底、Esc 打断监听、`/reload` 热加载、文件预读。

### llm.py — 对话循环
- `LLMBackend` 抽象 + `DeepSeekBackend`（**单 Pro 模型**——固定单模型养肥 DeepSeek 前缀缓存）。
- `run_conversation` 工具调用循环：流式优先、失败降级非流式、指数退避重试。
- 工具执行：连续 SAFE 工具并行（`_execute_tool_batch`）、流式期间 eager 预执行 SAFE 工具。
- `_handle_tool_results` 单一实现：解析 → 执行 → stormBreaker → 压缩 → 写 log。
- 上下文管理：超 65% 触发滑窗压缩（LLM 摘要驱旧）、超阈值熔断、单轮请求数熔断。
- 缓存统计/取证：每请求记 hit/miss + 结构指纹，供 `/context` 归因展示。

### cache_context.py — 三段式上下文
- **Immutable Prefix**：系统提示/记忆索引/目标/反思，会话级冻结、`send()` 时哈希校验。
- **Append-Only Log**：user/assistant/tool 只追加。
- **Volatile Scratch**：纯内存，不发 API。
- `send()` 纯追加（reasonix 式 canonical）：前缀在前、其后只追加 log 的非 volatile 消息，
  **永不挂尾**。`_repair_tool_pairing` 在此唯一咽喉补全中断留下的光杆 tool_calls，防 400 卡死。

### 可信审计（现状）
- `gate_audit`：仅会话首轮核对一次 HEAD 树是否在质量门通过记录里，挡 `--no-verify` 等绕门；
  不是轮末机制。
- `completion_audit`（谎报完成检测）已随 2026-06-25 的轮末审计整体移除一并删除，取证逻辑
  仅存在 git 历史里，需要时可单独捞回。
- `citation_audit.py`（编造引用检测）文件和测试还在，但已无任何调用点——是死代码，等待
  决定删除还是重新接线。
- 轮末不再运行任何审计（`_run_post_turn_audits` 已整体移除：判断型 nudge 一直弹、无明显
  效果，还拖累客观审计可信度）。

### 安全边界
- `guard` / `tool_guard`：C3 限 cwd、C10 读后写、C14 目录、P1 禁 git add -A、P2 禁 force-push。
- `file.py`：不可变安全核拒写（`is_immutable`），核心业务可写但走 import 冒烟安全带。
- pre-commit：密钥扫描 + ruff（裸 except / 函数行数 / lint 零错，按已开规则）。pytest 不进提交门（太重），靠手动跑 / 评审兜底。

### 记忆
- `memory`（remember/recall/forget），显式调用驱动，不再有自动收割。
- 记忆索引会话开始注入缓存前缀（轴①"越用越懂你"）。
- `lesson` / `experience` / `user_model` / `project_model` 等会话收尾 LLM 收割子系统已于
  2026-06-23 整体删除（文件不存在），记忆改为「用出来的」——agent 显式 remember 才写入。

### 任务
- `tasks`：`current.md`（唯一活跃）+ `pending/` + `archive/`，含目标锚点、gaps 报告。
- `task_loop`：agent 真推进 `current.md` 后自主续跑后续步骤，停由 agent 判断（标 [!] / 全 [x] / 不再推进）。

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
