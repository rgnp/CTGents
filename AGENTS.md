# AGENTS.md — AI 编程智能体操作手册

> 本文档面向在此项目中工作的 AI 编程智能体（如 Claude Code、Cline、Copilot 等）。
> 人类开发者请阅读 [README.md](./README.md)。

---

## 技术栈

- **语言**: Python 3.11+
- **框架**: 纯 Python（无 Web 框架）
- **依赖管理**: pip + requirements.txt + pyproject.toml (setuptools)
- **LLM 后端**: DeepSeek V3/V4 API（Flash + Pro 双模型）
- **搜索**: Tavily Search API
- **终端**: prompt_toolkit（增强交互）
- **网页解析**: trafilatura
- **代码检查**: ruff
- **测试**: pytest（120 个用例）
- **CI**: GitHub Actions

---

## 命令

| 命令 | 用途 |
|------|------|
| `python run.py` | 启动 Agent |
| `pytest` | 运行测试 |
| `pytest -v` | 详细测试输出 |
| `pip install -r requirements.txt` | 安装依赖 |
| `make test` | 运行测试 |
| `make lint` | 代码检查（ruff） |
| `make lint-fix` | 自动修复 lint 问题 |
| `make run` | 启动 Agent |
| `make check` | 项目规范扫描 |
| `make docs-sync` | 文档同步检查 |
| `make preflight` | 一站式检查：lint + test + docs-sync + check |
| `make precommit` | 运行 pre-commit 检查 |

---

## 项目结构

├── docs/                   # 文档
│   ├── architecture.md     #   系统架构
│   ├── development.md      #   开发者指南
│   ├── skills.md           #   Skill 技能包参考
│   ├── cache-design.md     #   DeepSeek 前缀缓存优化设计
│   ├── features.md         #   功能列表
│   ├── roadmap.md          #   开发路线图
│   ├── changelog.md        #   版本变更记录
│   └── contributing.md     #   贡献指南
│   │   ├── code.py         #   代码搜索（grep_code）
│   │   ├── git.py          #   Git 操作
│   │   ├── project.py      #   项目扫描
│   │   ├── lint.py         #   项目规范检查 + 文档同步检查
│   │   ├── web.py          #   网页搜索/阅读
│   │   ├── memory.py       #   记忆系统
│   │   ├── think.py        #   策略思考
│   │   ├── discover.py     #   能力扫描
│   │   ├── plugin_mgr.py   #   插件管理
│   │   ├── rag.py          #   RAG 代码索引 + 语义搜索
│   │   └── tokens.py       #   Token 预算
│   ├── safety.py           #   Auto Mode 安全系统
│   ├── commands.py         # 指令系统（/help /save /load 等）
│   ├── config.py           # 配置管理（环境变量 + 常量）
│   ├── llm.py              # LLM 对话循环 + 双模型路由
│   ├── main.py             # 主入口 + UI 循环
│   └── session.py          # 会话持久化
├── docs/                   # 文档
│   ├── architecture.md     #   系统架构
│   ├── development.md      #   开发者指南
│   ├── skills.md           #   Skill 技能包参考
│   ├── features.md         #   功能列表
│   ├── roadmap.md          #   开发路线图
│   ├── changelog.md        #   版本变更记录
│   └── contributing.md     #   贡献指南
├── tests/                  # 93 个测试用例
├── memory/                 # 跨会话记忆（Markdown 文件）
├── sessions/               # 对话历史（自动管理，已 .gitignore）
├── plugins/                # 自学安装的插件（自动管理，已 .gitignore）
├── AGENTS.md               # 本文档（面向 AI）
├── README.md               # 人类阅读的项目说明
├── Makefile                # 构建/测试/运行
├── pyproject.toml          # 包配置 + ruff + pytest
├── requirements.txt        # 依赖列表
├── .env.example            # 环境变量模板
├── .gitignore              # Git 排除规则
├── .editorconfig           # 编辑器统一配置
├── .pre-commit-config.yaml # 提交前自动检查
└── run.py                  # 启动入口
```

---

## 代码风格

- **命名**: snake_case 用于函数/变量，PascalCase 用于类，UPPER_SNAKE 用于常量
- **类型注解**: 公共函数必须有类型注解
- **文档字符串**: 模块和公共函数必须有 docstring
- **行宽**: 120 字符
- **导入顺序**: 标准库 → 第三方库 → 项目内部（空行分隔）
- **工具注册模式**: 每个 tools/ 模块定义 `TOOLS_XXX` 列表 + `execute(name, args)` 函数
- **指令注册模式**: 使用 `@builtin(name)` 或 `@builtin_multi(names)` 装饰器

---

## Git 工作流

- **分支命名**: `feat/描述`、`fix/描述`、`refactor/描述`
- **提交信息**: 中文简短描述，如 `feat: 添加项目规范检查工具`
- **提交前标准流程**:
  1. `make lint` — 代码检查
  2. `make test` — 测试通过
  3. `make docs-sync` — 文档同步检查
  4. `make check` — 规范评分 ≥ 80
  5. `git commit` — 提交
- **PR 要求**: 至少包含功能说明和测试验证

---

## CI/CD

每次 push / PR 到 master，GitHub Actions 自动运行：
1. **Lint**: `ruff check src/`
2. **Tests**: `pytest -v`（93 个用例）
3. **Spec**: `check_project` 评分（低于 80 分报错）
4. **Docs Sync**: `docs_sync_check`（违规阻止合并）

配置见 `.github/workflows/ci.yml`。

---

## 边界

### ✅ 始终执行（Always）
- 读取文件、搜索代码、查看 Git 状态
- 运行 `pytest` 测试
- 读取项目配置文件
- 生成 AGENTS.md 或 README.md
- 运行 `make docs-sync` 检查文档同步

### ⚠️ 事先询问（Ask First）
- 修改 `src/llm.py`、`src/config.py` 等核心模块
- 添加新的 pip 依赖
- 删除 memory/ 中的记忆文件
- 修改 `.env` 或 `.env.example`
- 执行 `git push` 到远程仓库
- 修改 `pyproject.toml`

### 🚫 绝不执行（Never）
- 提交 `.env` 文件或 API 密钥
- 修改 `sessions/` 中的对话历史
- 修改 `plugins/` 中的插件代码（除非用户明确要求）
- 执行 `git push --force` 到 main/master
- 删除 `.git` 目录

---

## 测试规范

- 测试框架: pytest
- 测试目录: `tests/`
- 运行命令: `pytest` 或 `python -m pytest`
- 覆盖率期望: 核心模块（tools/）覆盖率 > 70%

---

## Auto Mode 安全系统

三级工具安全等级和手动/自动双模式：

| 等级 | 说明 | 示例工具 |
|------|------|----------|
| ✅ SAFE | 只读操作，自动放行 | read_file, git_status, search_web |
| ⚠️ RISKY | 修改文件但可逆 | write_file, run_command, git_commit |
| 🚫 DANGEROUS | 破坏性/不可逆 | git_push |

**模式：**
- `manual`（默认）— RISKY + DANGEROUS 需要用户确认
- `auto` — SAFE 和 RISKY 自动放行，DANGEROUS 仍需确认

**会话信任：** 用 `/trust <工具名>` 将工具加入信任列表，本会话内自动放行。

---

## 工具模块开发规范

新增工具模块时，遵循以下模板：

```python
"""模块 docstring。"""

# ── 工具定义 ──
TOOLS_XXX = [
    {
        "type": "function",
        "function": {
            "name": "tool_name",
            "description": "工具描述",
            "parameters": {
                "type": "object",
                "properties": { ... },
                "required": [...],
            },
        },
    },
]

# ── 核心函数 ──
def execute(name: str, args: dict) -> str | None:
    if name == "tool_name":
        return do_something(args)
    return None
```

然后在 `src/tools/__init__.py` 的 `_BUILTIN_MODULES` 中注册，在 `src/safety.py` 标注安全等级。

---

## 文档同步规则（硬性要求）

> **写代码只是完成了一半，同步更新文档才算完成。**

每次修改代码后，必须同步更新以下对应文档：

| 修改的文件 | 必须同步更新的文档 |
|------------|-------------------|
| `src/llm.py`、`src/safety.py` | AGENTS.md、docs/architecture.md |
| `src/commands.py` | AGENTS.md、README.md |
| `src/main.py` | README.md、docs/architecture.md |
| `src/tools/*.py` | AGENTS.md |
| `src/config.py` | .env.example、README.md |
| `tests/` | docs/changelog.md |
| `pyproject.toml`、`Makefile` | README.md、AGENTS.md、docs/changelog.md |
| `.github/workflows/` | README.md、AGENTS.md |
| AGENTS.md | README.md |
| README.md | AGENTS.md |

`docs_sync_check` 工具可在 commit 前调用，自动检查是否有违反规则。

---

## 安全

- API 密钥通过 `.env` 文件管理，绝不硬编码
- `.env` 已在 `.gitignore` 中
- Shell 命令执行有黑名单拦截（`rm -rf /`、`shutdown` 等）
- 文件写入自动备份到 `~/.agent_backups/`
- 所有工具调用结果需经过 Token 预算截断
