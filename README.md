# CTGents — 自进化 AI 编程助手

终端里的 AI 编程助手。能写代码、搜索网络、管理知识库，通过多层机械安全门禁保护自身不被破坏。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入密钥
py scripts/install_hooks.py   # 安装 Git 提交钩子
py src/main.py                # 启动
```

## 核心机制

### 机械安全门禁

规则不是靠 LLM 自觉——到不了的防线代码兜底：

| 层 | 管什么 | 怎么拦 |
|---|---|---|
| 工具边界 | 文件操作限 cwd、读后写、禁 src/tools/ 新建 | `tool_guard.py` 在 write_file/edit/delete 前机械校验 |
| 文件保护 | 禁止修改 guard.py 等 9 个核心文件 | `file.py` → `is_protected()` 匹配 PROTECTED_FILES |
| 覆盖率门禁 | 未测试文件拒绝修改 | `coverage_gate.py` 四层递进阈值 |
| 提交闸 | lint 零错误 + 全量 pytest | pre-commit hook，任何路径提交都绕不过 |
| 事后审计 | 改代码没跑测试 → 下轮提醒 | `_inject_completion_audit` 每轮机械注入 |
| 记忆收割 | 会话关闭自动提取失败模式 | `_finalize_session` → `extract_lessons` + `save_lessons` |
| Tavily 自愈 | quota 耗尽自动切 key | search_web + eager executor 双层兜底 |

### 记忆→行为闭环

打破"跨会话记住了但不改变行为"的死结：

- **检测失败**：四指纹检测器（签名漂移/重复编辑/工具参数错/预提交拒）
- **自动收割**：会话关闭时机械提取教训存入 memory/，不靠 LLM 自觉
- **下次注入**：匹配失败模式时自动在上下文尾部注入 `[⚠️ 经验提醒]`
- **工具自愈**：search_web quota 耗尽自动重读 .env 并轮换 key

### DeepSeek 前缀缓存

三段式 CacheContext：不可变 prefix + 只追加 log + 易失 scratch。日常编码命中率 94-96%。

## AGENTS.md — AI 操作手册

[AGENTS.md](AGENTS.md) 是给 AI 看的操作手册。最近重构为三层：

- `[必须]` 8 条 LLM 操心的规则 + 11 条禁止
- `[后台]` 11 行机械保障清单（已代码强制，一眼扫过）
- 行为准则（节奏/任务追踪/沟通/记忆）

## 项目结构

```
src/
  main.py              # 主入口 + 管线注入
  llm.py               # LLM 后端 + eager 并行执行
  cache_context.py     # 三段式上下文管理器
  commands.py          # 指令系统
  config.py            # 配置中心 + MultiKeyTavilyClient
  session.py           # 会话持久化
  guard.py             # 自我保护（PROTECTED_FILES 9 个关键文件）
  coverage_gate.py     # 覆盖率门禁（4 tier）
  validate.py          # 三阶段验证（AST→pytest→覆盖率/lint）
  lesson.py            # 教训提取 + 记忆存储
  tracker.py           # 调用追踪 + 被动反思
  tools/
    file.py, web.py, exec.py, git.py, code.py
    rag.py, evolve.py, memory.py, self.py, storm.py ...
tests/                 # 677 个测试用例
scripts/               # Git hooks 安装脚本
AGENTS.md              # AI Agent 操作手册
```

## 许可证

[MIT](LICENSE)
