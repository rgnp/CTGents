<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?style=flat-square" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/tests-789%20passed-22c55e?style=flat-square" alt="789 tests">
  <img src="https://img.shields.io/badge/license-MIT-8b5cf6?style=flat-square" alt="MIT">
  <img src="https://img.shields.io/badge/status-active-06b6d4?style=flat-square" alt="Active">
</p>

<br>

<p align="center">
  <strong>CTGents</strong> &nbsp;·&nbsp;
  自进化 AI 编程助手
  <br>
  <sub>终端里的 agent。不是又一层 LLM wrapper——</sub>
  <br>
  <sub>安全不是靠自觉，是代码兜底。</sub>
</p>

<br>

---

## 这是什么

终端里的 AI 编程助手。能写代码、搜网络、管知识库。核心区别在两层：

**第一层：安全机制是机械的，不是自觉的。**
写文件限工作目录没商量、读后才让改、不可变核心文件 agent 根本动不了、提交前 lint+测试代码替你强检——不是"希望你别做"，是"你做不了"。

**第二层：越用越有用，不是每次从零开始。**
跨会话的用户理解、项目知识、失败教训自动收割。同类错误第二次会被提前拦住。新会话自动知道你上次做到哪。

---

## 快速开始

```bash
git clone https://github.com/rgnp/CTGents.git
cd CTGents

pip install -r requirements.txt
cp .env.example .env              # 填入 API 密钥
python scripts/install_hooks.py   # 安装 Git 提交钩子
python src/main.py                # 启动
```

> `.env` 需要至少一个 DeepSeek API key。Tavily key 可选（不配则 web search 不可用）。

支持行式 REPL 和 **TUI**（默认自动检测）。输入 `/help` 查看指令列表。

---

## 能力

### 核心循环

```
用户输入 → 自动路由(Pro/Flash) → 工具循环 → 审计 → 记忆
           ├── 代码读写/分析/搜索
           ├── 网络搜索/论文检索
           ├── Git 操作(commit/push/PR)
           ├── 知识库索引/RAG
           └── 计划审查(每 N 步方向检查)
```

### 工具索引

| 类 | 工具 |
|---|---|
| 代码 | `read_file` · `write_file` · `edit_file_lines` · `grep_code` · `replace_in_file` · `analyze_code` · `scan_project` · `find_files` |
| 执行 | `run_python` · `run_command` · `run_async` · `poll` |
| 网络 | `search_web` · `read_page` · `learn` |
| 论文 | `scan_papers` · `read_papers` · `analyze_paper` · `cross_validate` · `paper_grid` · `save_paper_card` |
| 知识 | `rag_index` · `rag_query` · `rag_search` · `remember` · `recall` · `forget` |
| Git | `git_status` · `git_diff` · `git_log` · `git_commit` · `git_push` · `git_pr` · `git_branch` · `git_review` |
| 项目 | `check_project` · `generate_agents_md` · `docs_sync_check` · `repo_clone` |
| 系统 | `self` · `think` · `pin` · `unpin` · `task_done` · `need_user` · `update_plan` · `load_psyche` |

---

## 架构亮点

### 三段式上下文（CacheContext）

针对 DeepSeek 前缀缓存优化的三段式架构：

```
┌──────────────────────────┐
│  IMMUTABLE PREFIX        │ ← 会话级冻结（AGENTS.md + 记忆索引 + 长期目标）
│  哈希锁死，不被污染      │
├──────────────────────────┤
│  APPEND-ONLY LOG         │ ← 用户/助手/工具结果，只追加不改
│  无挂尾，无 volatile 尾  │    实测命中率 88-96%
├──────────────────────────┤
│  VOLATILE SCRATCH        │ ← 纯内存，不发 API
└──────────────────────────┘
```

### 机械安全门禁

| 边界 | 规则 | 实现 |
|------|------|------|
| 文件 | 限工作目录、读后写、禁 root 新建 `.py` | `tool_guard.check()` 执行前拦截 |
| 核心文件 | guard.py/tool_guard.py — agent 改不动 | `is_immutable()` 在 write/edit/delete 拦截 |
| 提交 | ruff + 快速测试强制通过 | pre-commit hook + gate_audit 事后审计 |
| 危险命令 | `git add -A` / `force-push main` / `rm -rf` 零容忍 | `_git_guard_block` + P1/P2 规则 |
| Shell 注入 | 元字符 `&|;<>` 拒绝执行 | `_split_command` |

### 记忆 → 行为闭环

不是"记住了但不改变行为"——失败模式四指纹检测，下回自动注入经验提醒：

```
检测 → 收割 → 存储 → 下次匹配 → 上下文注入 → 防复发
```

---

## Psyche — 领域认知框架

跨会话的"行为一致性"机制。不是知识库，是思维方式：

```
加载 psyche → 改变判断准则 → 同类场景做同类决定
```

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
├── llm.py                # LLM 后端 + 工具循环（eager 并行 + 规划审查）
├── cache_context.py      # 三段式上下文管理器
├── system_context.py     # 可刷新的上下文源（OpenCode 模式）
├── commands.py           # 指令系统（/help /clear /psyche 等）
├── guard.py              # 文件分级（不可变 / 核心 / 自由）
├── task_loop.py          # 长任务自主续跑 + 动态重规划
├── tasks.py              # current.md 的读写/创建/更新/归档
├── tools/                # 61 个工具，19 个模块
│   ├── tool_guard.py     # 工具边界机械校验
│   ├── file.py           # 文件读写 + 备份回滚
│   ├── exec.py           # 命令执行 + Git 护栏
│   ├── control.py        # task_done / need_user / update_plan
│   ├── storm.py          # 工具调用去重（滑动窗口）
│   └── ...
├── status_bar.py         # 底部状态条（ctx 充满度 / 缓存命中 / 任务进度）
├── psyche_bridge.py      # Psyche 注入/卸载
├── session_pins.py       # 会话钉板（关键约束持久化）
├── organs.py             # 器官生命体征（只读产物派生）
└── ...
tests/                    # 789 个测试（129 个 @slow）
scripts/
├── git-hooks/pre-commit  # 提交钩子（ruff + 密钥扫描）
└── install_hooks.py      # 钩子安装器（core.hooksPath）
psyche/                   # 领域认知框架
knowledge/                # 研究知识库（论文/笔记）
tasks/                    # 长任务追踪
memory/                   # 跨会话记忆
docs/                     # 文档
```

---

## 依赖

- **Python 3.12+**
- **DeepSeek API** — 模型调用（必须）
- **Tavily API** — 网页搜索（可选，不配则 search_web 不可用）
- **Semantic Scholar API** — 论文检索（可选，scan_conf 用）

安装：`pip install -r requirements.txt`

---

## 许可证

[MIT](LICENSE)
