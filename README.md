<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-1100%2B-22c55e?style=flat-square" alt="1100+ tests">
  <img src="https://img.shields.io/badge/license-MIT-8b5cf6?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/status-active-06b6d4?style=flat-square" alt="Active">
</p>

<br>

<p align="center">
  <strong>CTGents</strong> &nbsp;·&nbsp;
  终端里的自进化 AI 编程助手
  <br>
  <sub>单 DeepSeek Pro · append-only 三段式上下文 · 机械安全门禁</sub>
  <br>
  <sub>不是又一层 LLM wrapper——安全不是靠自觉，是代码兜底。</sub>
</p>

<br>

---

## 这是什么

终端里的 AI 助手。能写代码、搜网络、管知识库、读论文。核心区别：

**第一层：安全机制是机械的，不是自觉的。**
写文件限工作目录、读后才让改、不可变核心文件 agent 根本动不了、提交前 lint 强制检查——不是"希望你别做"，是"你做不了"。

**第二层：越用越有用，不是每次从零开始。**
跨会话记忆显式写入（`remember`，非自动收割）。失败教训命中同类场景时尾部提醒，同类错误第二次会被提前拦住。

**第三层：自进化工具。**
36 个常驻工具 + 按需研究工具组；不用的能力不占前缀上下文。

---

## 快速开始

```bash
git clone https://github.com/rgnp/CTGents.git
cd CTGents

pip install -e ".[dev]"
cp .env.example .env              # 填入 DeepSeek API key + Tavily API key
python scripts/install_hooks.py   # 安装 Git 提交钩子
python src/main.py                # 启动（自动检测 TUI/行式 REPL）
```

> 需要至少一个 DeepSeek API key 和一个 Tavily API key。支持 Tavily 多 key 轮换（逗号分隔）。
> 个人数据默认写到 `~/.ctgents`；可在 `.env` 设置 `CTG_WORKSPACE_DIR`。

输入 `/help` 查看指令列表，`/tools load research` 挂载文献研究工具。
核心项目与个人积累的边界见 [`docs/workspace.md`](docs/workspace.md)。

---

## 核心架构

### 三段式上下文（CacheContext）

针对 DeepSeek 前缀缓存优化的三段式，实测缓存命中率 88–96%：

```
┌────────────────────────────────────┐
│  IMMUTABLE PREFIX                  │ ← 会话级系统提示，哈希锁死
│  哈希校验，被改即抛异常            │   会话期间不变，服务端缓存保持热状态
├────────────────────────────────────┤
│  APPEND-ONLY LOG                   │ ← 用户/助手/工具调用/结果，只追加
│  中段视图变换：陈旧大工具结果→stub │   配对修复：光杆 tool_calls 补占位
├────────────────────────────────────┤
│  VOLATILE SCRATCH                  │ ← 纯内存，不发 API
└────────────────────────────────────┘
```

`send()` 组装消息时执行两层协议修复：

1. **中段折叠** — 超出 N 轮前的超长工具结果自动折成一行 stub（原文在 self.log 不动，落盘+可 recall），减少 API token。阈值和轮数由 `params.py` 控制，一个开关全关，完全可逆。
2. **配对修复** — 工具执行中断/崩溃会在 log 里留下"光杆 tool_calls"（缺结果），每轮重发都 400，会话卡死。`send()` 对这些缺失的工具结果补占位消息，保配对完整性。

### 单模型缓存优先

固定使用 **DeepSeek v4 Pro**（单模型养肥前缀缓存），Flash 作为可选的备用模型。不走双模型自动路由——那会分裂 KV 缓存命名空间，抵消缓存收益。

### 对话循环

```
用户输入 → _drive_turn（LLM 调用 + 工具循环）
         → 若推进了任务则 run_task_continuation（自主续跑后续步骤）
         → 落盘会话 + 冻结前缀
```

### 机械安全门禁

| 边界 | 规则 | 实现 |
|------|------|------|
| 文件 | 限工作目录、读后写、禁 root 新建 `.py` | `tool_guard.check()` 执行前拦截 |
| 核心文件 | guard.py/tool_guard.py — agent 改不动 | `is_immutable()` 在 write/edit/delete 拦截 |
| 提交 | ruff 强制通过 | pre-commit hook + gate_audit 事后审计 |
| 危险命令 | `git add -A` / `force-push main` / `rm -rf` 零容忍 | `_git_guard_block` + P1/P2 规则 |
| Shell 注入 | 元字符 `&|;<>` 拒绝执行 | `_split_command` |

### 记忆 → 行为闭环

`remember` 显式写入 → fingerprint 去重合并 → 关键词/bigram 检索（`recall`，非语义/无 embedding）→
显式 `adopt_asset` → 任务结果关联。同 fingerprint 的记忆自动合并（不散成 N 条），旧记忆按时间衰减降权。
已有任务结果后可用 `feedback_asset` 显式记录 helpful/misleading 和理由。检索命中不等于采用，
任务通过也不自动等于这条记忆有效；审计只报告证据与清理候选，不代替人的价值判断或自动删除。
会话结束不再自动收割 user_model/project_model（2026-06-23 已整体移除）——记忆是用出来的，
agent 要显式判断值得记才会写入。

### 统一工作回执

Task 与 Heartbeat 保留各自状态机，但共享不可变工作回执：工作 ID、验收证据哈希、工作区指纹、
产物 SHA-256 和跨系统引用。Heartbeat 摘要送达后需要
`/heartbeat accept|revise|reject` 显式处置；`/organs` 汇总待处置交还和版本化产物。
设计边界见 [`docs/work-lifecycle.md`](docs/work-lifecycle.md)。

### 工具组按需加载（load-on-demand）

文献研究工具（`scan_papers`、`read_papers`、`read_paper`、`scan_conf`、`rag_index_research`）默认不挂常驻前缀，节省 ~580 token/轮。做科研时用 `/tools load research` 挂上。

---

## 工具索引（36 个默认，研究组按需加载）

| 类 | 工具 |
|---|---|
| 代码 | `read_file` · `write_file` · `replace_in_file` · `edit_file_lines` · `delete_file` · `grep_code` · `find_files` · `list_files` · `count_lines` · `move_file` · `make_dir` · `analyze_code` · `scan_project` |
| 执行 | `run_python` · `run_command` · `run_async` · `poll` |
| 网络 | `search_web` · `read_page` · `learn` |
| 文献（按需） | `scan_papers` · `read_papers` · `read_paper` · `scan_conf` · `rag_index_research` |
| 知识 | `rag_index` · `rag_query` · `rag_search` · `rag_status` · `remember` · `recall` · `forget` |
| Git | `git_status` · `git_diff` · `git_log` · `git_branch` · `git_commit` · `git_push` · `git_restore` · `git_review` |
| 项目 | `repo_clone` · `repo_status` |
| 系统 | `self` · `think` · `task_done` · `need_user` · `update_plan` |
| Psyche | `load_psyche` · `unload_psyche` |

### 工具调用缓存（双层）

- **Storm（轮内去重）**：同一轮循环中重复的工具调用直接返回缓存结果
- **会话缓存（跨轮次）**：读工具（`grep_code`/`find_files`/`count_lines`/`read_page`/`scan_papers`/`read_papers`）5 分钟 TTL，最多 200 条目

---

## Psyche — 领域认知框架

跨会话的"行为一致性"机制。不是知识库，是思维方式——加载后改变判断准则，同类场景做同类决定。

```
加载 psyche → 改变判断准则 → 同类场景做同类决定
```

已内置：
- **software-development** — 代码审查、架构判断、安全编码
- **agent-development** — Agent 系统特有陷阱、工具设计、循环控制
- **aesthetic-design** — TUI 审美、配色、终端 UX
- **learning-method** — 深度阅读外部项目/论文的方法论
- **testing** — FIRST 原则、AAA 模式、测试速度优化

加载：`/psyche load <name>`。自动持久化，跨会话有效。

---

## 项目结构

```
src/
├── main.py               # 主入口 + 一轮对话管线
├── llm.py                # LLM 对话循环 + 工具循环（SAFE 并行/滑窗压缩）
├── cache_context.py      # 三段式上下文管理器（前缀冻结 + 纯追加）
├── system_context.py     # 可刷新的上下文源（OpenCode 模式）
├── commands.py           # 指令系统（/help /clear /psyche 等）
├── guard.py              # 文件分级（不可变/核心/自由）
├── task_loop.py          # 长任务自主续跑 + 动态重规划
├── tasks.py              # current.md 读写/创建/更新/归档
├── tools/                # 原子工具与安全调度
│   ├── tool_guard.py     # 工具边界机械校验
│   ├── file.py           # 文件读写 + 备份回滚
│   ├── exec.py           # 命令执行 + Git 护栏
│   ├── storm.py          # 工具调用去重
│   └── ...
├── tui.py                # Textual 全屏 TUI（默认界面）
├── status_bar.py         # 底部状态条
├── psyche_bridge.py      # Psyche 注入/卸载
├── ctgents_resources/    # 可发布的内置 psyche/skills
├── session.py            # 会话 JSON 持久化 + 冻结前缀存盘
├── organs.py             # 器官生命体征
├── params.py             # 所有可调行为旋钮
└── config.py             # 密钥/模型/路径 + MultiKeyTavilyClient
tests/                    # 单元、契约与集成测试
scripts/
├── git-hooks/pre-commit  # 提交钩子（ruff + 密钥扫描）
└── install_hooks.py      # 钩子安装器
docs/                     # 架构/设计/变更日志
```

个人工作区是独立项目：

```text
<CTG_WORKSPACE_DIR>/
├── memory/
├── knowledge/
├── sessions/
├── tasks/
└── stats/
```

---

## 长期目标

- **知识积累不丢失** — 搜过的知识、对话洞见，跨会话可检索
- **行为一致性** — 同类情况做同类决定，不每次从零推理
- **主动性** — 空闲时推进长期目标，不等指令
- **判断力** — 给判断而不是给选项，决定然后执行
- **动态 Replanning** — 长任务每步完成后检查当前状态是否仍匹配计划，偏差了就重新分解
- **写入时记忆分类** — 每次 remember 前过一道门：持久/跨会话/工作记忆
- **上下文自适应压缩** — 超阈值时主动压缩中间推理，保留关键事实
- **Sub-agent 隔离** — 同时追踪多个独立任务时按任务隔离 context

---

## 依赖

- **Python 3.11+**
- **DeepSeek API key** — 模型调用（必须）
- **Tavily API key** — 网页搜索（必须，支持多 key 轮换）
- **Semantic Scholar API key** — 论文检索（可选，`scan_conf` 用）

安装核心开发环境：`pip install -e ".[dev]"`；需要本地语义检索和 PDF 论文工具时安装
`.[research]`。其中 PyMuPDF 采用 AGPL 3.0 / 商业双许可证，不属于 CTGents 的 MIT
许可；分发或部署前请确认你的使用方式符合其许可证要求。

---

## 开发

```bash
make lint        # ruff 检查
make test        # 运行测试
make lint-fix    # 自动修复
make preflight   # lint + test + docs-sync + check 一站式
```

参见 [`docs/development.md`](docs/development.md) 了解如何添加工具/指令。

---

## 许可证

[MIT](LICENSE)
