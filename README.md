<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License MIT">
  <img src="https://img.shields.io/badge/tests-677%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-gated-success.svg" alt="Coverage Gated">
</p>

# CTGents — 自进化 AI 编程助手

终端里的 AI 编程助手。能写代码、搜索网络、管理知识库，通过**多层机械安全门禁**保护自身不被破坏。

> 不是又一个 LLM wrapper。规则不是靠 AI 自觉——到不了的防线代码兜底。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env              # 编辑 .env 填入 API 密钥
python scripts/install_hooks.py   # 安装 Git 提交钩子
python src/main.py                # 启动
```

---

## 能力概览

| 类别 | 能力 |
|------|------|
| 代码 | 读写文件、静态分析、运行测试、语义搜索 |
| 网络 | 网页搜索、论文检索（arxiv / Semantic Scholar）、URL 阅读 |
| 知识 | 知识库索引与检索、论文卡片管理、交叉验证 |
| Git | 提交、审查、分支、PR、日志 |
| 成长 | 越用越懂你：跨会话收割用户理解、项目知识、失败教训 |
| 监控 | 独立的只读 Web 面板（`python -m dashboard.server`）|

---

## 核心机制

### 机械安全门禁

规则不是靠 LLM 自觉——到不了的防线代码兜底：

| 层 | 管什么 | 怎么拦 |
|---|---|---|
| 工具边界 | 文件操作限 cwd、读后写、禁 src/tools/ 新建 `.py` | `tool_guard.py` 在执行前机械校验 |
| 文件保护 | 禁止修改 guard.py 等核心文件 | `file.py` → `is_immutable()` |
| 提交闸 | lint 零错误 + 全量 pytest | pre-commit hook，任何路径提交都绕不过 |
| 事后审计 | 改代码没跑测试 → 下轮提醒 | `_inject_completion_audit` 每轮注入 |
| 记忆收割 | 会话关闭自动提取失败模式 | `_finalize_session` → `extract_lessons` |
| Tavily 自愈 | API quota 耗尽自动切 key | `MultiKeyTavilyClient` 轮换 + 热重载 |

### 记忆 → 行为闭环

打破"跨会话记住了但不改变行为"的死结：

- **检测失败**：四指纹检测器（签名漂移 / 重复编辑 / 工具参数错 / pre-commit 拒）
- **自动收割**：会话关闭时机械提取教训存入 `memory/`
- **下次注入**：匹配失败模式时自动在上下文尾部注入 `[⚠️ 经验提醒]`
- **工具自愈**：search_web quota 耗尽自动重读 `.env` 并轮换 key

### DeepSeek 前缀缓存

三段式 CacheContext：不可变 prefix + 只追加 log + 易失 scratch。日常编码命中率 **94-96%**。

---

## 监控面板

独立的只读 Web 面板，与 agent 进程完全解耦：

```bash
python -m dashboard.server          # http://127.0.0.1:8765
```

四个视图：总览（健康 / 命中率 / Token）· 安全门禁 · 记忆教训 · 进化日志。5 秒自动刷新，agent 重启不影响。

---

## AGENTS.md — AI 操作手册

[AGENTS.md](AGENTS.md) 是给 AI 看的操作手册，三层结构：

- **必须遵守**：8 条 LLM 操心的规则 + 11 条禁止
- **后台保障**：11 行机械保障清单（已代码强制）
- **行为准则**：节奏 / 任务追踪 / 沟通 / 记忆边界

---

## 项目结构

```
src/
  main.py              # 主入口 + 管线注入
  llm.py               # LLM 后端 + eager 并行执行
  cache_context.py     # 三段式上下文管理器
  commands.py          # 指令系统
  config.py            # 配置中心 + MultiKeyTavilyClient
  guard.py             # 自我保护（不可变文件保护）
  validate.py          # 三阶段验证（AST → pytest → 覆盖率 / lint）
  lesson.py            # 教训提取 + 记忆存储
  autonomic.py         # 自主神经系统（第 4 层）
  tools/               # 12 个工具模块
dashboard/             # 只读监控面板
tests/                 # 677 个测试用例
memory/                # 跨会话记忆
knowledge/             # 研究知识库
```

---

## 许可证

[MIT](LICENSE)
