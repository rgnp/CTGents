# AGENTS.md — AI 编程智能体操作手册

> 本文档面向在此项目中工作的 AI 编程智能体（如 Claude Code、Cline、Copilot 等）。
> 人类开发者请阅读 README.md。

---

## 技术栈

- Python 3.11+

## 命令

| 命令 | 用途 |
|------|------|
| `pip install -e .` | 构建/安装依赖 |
| `pip install -r requirements.txt` | 构建/安装依赖 |
| `pytest` | 运行测试 |
| `make` | 查看所有可用目标 |

## 项目结构

```
├── .github/
│   └── workflows/
├── .rag-index/
│   ├── hashes.json
│   ├── index.json
│   └── meta.json
├── agent/

├── docs/
│   ├── architecture.md
│   ├── cache-design.md
│   ├── changelog.md
│   ├── contributing.md
│   ├── development.md
│   ├── features.md
│   ├── roadmap.md
│   └── skills.md
├── memory/
│   ├── agent-tool-usage-efficiency.md
│   ├── ai-agent-dev-standards-six-dimensions.md
│   ├── dev-workflow-docs-must-sync.md
│   ├── MEMORY.md
│   ├── test-sync-rule.md
│   ├── token-efficiency-principle.md
│   ├── user-grade.md
│   ├── user-major-direction.md
│   ├── user-nickname.md
│   ├── user-prefers-adaptive-verbosity.md
│   ├── user-prefers-efficient-execution.md
│   ├── user-prefers-execution-efficiency.md
│   ├── user-prefers-flexible-judgment.md
│   ├── user-prefers-minimal-output.md
│   ├── user-prefers-no-more-questions.md
│   ├── user-prefers-no-redundant-reads.md
│   ├── user-prefers-plan-first.md
│   └── user-prefers-token-efficiency.md
├── src/
│   ├── tools/
│   ├── __init__.py
│   ├── cache_context.py
│   ├── commands.py
│   ├── config.py
│   ├── llm.py
│   ├── main.py
│   ├── safety.py
│   ├── session.py
│   └── suggest.py
├── stats/
│   ├── .json
│   ├── 2026-05-30-131659.json
│   └── 2026-05-31-073558.json
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_cache.py
│   ├── test_cache_context.py
│   ├── test_exec.py
│   ├── test_lint.py
│   ├── test_project.py
│   ├── test_safe.py
│   ├── test_safety.py
│   └── test_storm.py
├── .editorconfig
├── .env
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── AGENTS.md
├── LICENSE
├── Makefile
├── pyproject.toml
├── README.md
├── requirements.txt
└── run.py
```

## 代码风格

- 命名: snake_case（函数/变量），PascalCase（类），UPPER_SNAKE（常量）
- 类型注解: 公共函数必须有
- 文档字符串: 模块和公共函数必须有
- 格式化: 使用 ruff 或 black
- Web 工具: `search_web` / `read_page` 有 TTL 缓存（搜索 5min，页面 10min，最大 200 条），带 15s 超时和 8000 字符截断

## Git 工作流

- 分支命名: `feat/描述`、`fix/描述`、`refactor/描述`
- 提交前: 运行测试确保不破坏现有功能
- 提交信息: 简洁描述变更内容

## 边界

### ✅ 始终执行（Always）
- 读取文件、搜索代码、查看 Git 状态
- 运行测试
- 读取项目配置文件

### ⚠️ 事先询问（Ask First）
- 修改核心模块
- 添加新依赖
- 执行 `git push` 到远程仓库
- 修改配置文件

### 🚫 绝不执行（Never）
- 提交 `.env` 文件或 API 密钥
- 执行 `git push --force` 到 main/master
- 删除 `.git` 目录

## 安全

- API 密钥通过 `.env` 文件管理，绝不硬编码
- `.env` 必须在 `.gitignore` 中
- 文件写入前自动备份
