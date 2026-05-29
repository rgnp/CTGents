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

---

## 命令

| 命令 | 用途 |
|------|------|
| `python run.py` | 启动 Agent |
| `pytest` | 运行测试 |
| `python -m pytest` | 运行测试（备选） |
| `pytest -v` | 详细测试输出 |
| `pip install -r requirements.txt` | 安装依赖 |

---

## 项目结构

```
agent/
├── src/                    # 核心源码
│   ├── tools/              # 工具模块（每个文件一个工具类别）
│   │   ├── __init__.py     #   工具注册与调度
│   │   ├── file.py         #   文件操作（读/写/删/行级编辑）
│   │   ├── exec.py         #   Shell 执行
│   │   ├── code.py         #   代码搜索（grep_code）
│   │   ├── git.py          #   Git 操作
│   │   ├── project.py      #   项目扫描
├── docs/                  # 文档
│   ├── architecture.md    #   系统架构
│   ├── development.md     #   开发者指南
│   └── skills.md          #   Skill 技能包参考
├── tests/                 # 93 个测试用例
├── AGENTS.md               # 本文档
├── README.md               # 人类阅读的项目说明
├── CHANGELOG.md            # 版本变更记录
├── CONTRIBUTING.md         # 贡献指南
├── ROADMAP.md              # 开发路线图
├── FEATURES.md             # 功能列表（详细版）
├── pyproject.toml          # 包配置 + ruff + pytest
├── requirements.txt        # 依赖列表
├── .env.example            # 环境变量模板
├── .editorconfig           # 编辑器统一配置
├── .pre-commit-config.yaml # 提交前自动检查
├── Makefile                # 构建/测试/运行
└── run.py                  # 启动入口
│   └── session.py          # 会话持久化
├── memory/                 # 跨会话记忆（Markdown 文件）
├── sessions/               # 对话历史（自动管理）
├── plugins/                # 自学安装的插件（自动管理）
## Auto Mode 安全系统

本系统提供三级工具安全等级和手动/自动双模式：

| 等级 | 说明 | 示例工具 |
|------|------|----------|
| ✅ SAFE | 只读操作，自动放行 | read_file, git_status, search_web |
| ⚠️ RISKY | 修改文件但可逆 | write_file, run_command, git_commit |
| 🚫 DANGEROUS | 破坏性/不可逆 | git_push |

**模式：**
- `manual`（默认）— RISKY + DANGEROUS 需要用户确认
- `auto` — SAFE 和 RISKY 自动放行，DANGEROUS 仍需确认

**会话信任：** 用 `/trust <工具名>` 将工具加入信任列表，本会话内自动放行。


├── tests/                  # 测试（待补充）
├── AGENTS.md               # 本文档
├── README.md               # 人类阅读的项目说明
## CI/CD

每次 push / PR 到 master，GitHub Actions 自动运行：
1. **Lint**: `ruff check src/`
2. **Tests**: `pytest -v`（93 个用例）
3. **Spec**: `check_project` 评分（低于 80 分报错）

配置见 `.github/workflows/ci.yml`。

---


├── ROADMAP.md              # 开发路线图
├── FEATURES.md             # 功能列表
├── learn_skill.md          # Skill 学习说明
├── pyproject.toml          # 包配置
├── requirements.txt        # 依赖列表
├── .env.example            # 环境变量模板
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
- **提交前**: 运行 `pytest` 确保不破坏现有功能
- **PR 要求**: 至少包含功能说明和测试验证

---

## 边界

### ✅ 始终执行（Always）
- 读取文件、搜索代码、查看 Git 状态
- 运行 `pytest` 测试
- 读取项目配置文件
- 生成 AGENTS.md 或 README.md
- 使用 `pytest` 运行测试

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

然后在 `src/tools/__init__.py` 的 `_BUILTIN_MODULES` 中注册。

---

## 安全

- API 密钥通过 `.env` 文件管理，绝不硬编码
- `.env` 已在 `.gitignore` 中
- Shell 命令执行有黑名单拦截（`rm -rf /`、`shutdown` 等）
- 文件写入自动备份到 `~/.agent_backups/`
- 所有工具调用结果需经过 Token 预算截断
