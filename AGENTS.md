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
| `pytest` | 运行测试（210 用例） |
| `make` | 查看所有可用目标 |

## 项目结构

```
├── .github/
│   └── workflows/
├── .rag-index/              # RAG 代码语义搜索索引
├── docs/                    # 设计文档
│   ├── architecture.md
│   ├── cache-design.md
│   ├── changelog.md
│   ├── contributing.md
│   ├── development.md
│   ├── features.md
│   ├── roadmap.md
│   └── skills.md
├── memory/                  # 长期记忆（Markdown + YAML frontmatter）
├── src/
│   ├── tools/               # 工具模块（14 个工具提供者 + 4 个基础设施）
│   │   ├── __init__.py      # 工具注册表、调度、热加载
│   │   ├── web.py           # search_web / read_page
│   │   ├── file.py          # read_file / write_file / read_file_lines / edit_file_lines
│   │   ├── exec.py          # run_python / run_command
│   │   ├── code.py          # grep_code
│   │   ├── think.py         # think
│   │   ├── memory.py        # remember / recall / forget
│   │   ├── git.py           # git_status / git_diff / git_log / git_commit / git_push / git_pr / git_branch
│   │   ├── project.py       # scan_project / get_project_context
│   │   ├── lint.py          # check_project / docs_sync_check / generate_agents_md
│   │   ├── mcp.py           # MCP 协议工具
│   │   ├── rag.py           # rag_query / rag_index / rag_status
│   │   ├── subagent.py      # 子代理生成
│   │   ├── storm.py         # 工具调用滑动窗口去重
│   │   ├── tracker.py       # 工具调用追踪（JSONL）
│   │   ├── reflect.py       # 失败反思记录
│   │   ├── tokens.py        # Token 预算管理
│   │   └── plugin_mgr.py    # 插件管理
│   ├── cache_context.py     # 三段式上下文管理器（前缀缓存核心）
│   ├── commands.py          # / 指令系统
│   ├── config.py            # 配置中心
│   ├── llm.py               # LLM 后端抽象 + 对话循环
│   ├── main.py              # 主入口
│   ├── safety.py            # 工具安全模式
│   ├── session.py           # 会话持久化
│   └── suggest.py           # 主动建议引擎（自愈闭环）
├── stats/                   # API 使用统计（按会话隔离）
├── tests/                   # 测试套件（210 用例）
│   ├── test_cache.py
│   ├── test_cache_context.py
│   ├── test_exec.py
│   ├── test_lint.py
│   ├── test_prefix_cache.py
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

## 架构要点

- **三段式上下文**: CacheContext（prefix/log/scratch），保障 DeepSeek 前缀缓存
- **Append-only log**: 对话历史只追加不删除，压缩改为末尾追加摘要
- **缓存优化**: 压缩阈值 85%，绝大多数会话无需压缩，缓存命中率 90%+

## 代码风格

- 命名: snake_case（函数/变量），PascalCase（类），UPPER_SNAKE（常量）
- 类型注解: 公共函数必须有
- 文档字符串: 模块和公共函数必须有
- 格式化: 使用 ruff 或 black
- Web 工具: `search_web` / `read_page` 有 TTL 缓存

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
