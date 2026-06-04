# CTGents — 自进化 AI 编程与科研助手

[![CI](https://github.com/rgnp/CTGents/actions/workflows/ci.yml/badge.svg)](https://github.com/rgnp/CTGents/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/rgnp/CTGents)](LICENSE)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek_V4-green)](https://deepseek.com)

终端里的自进化 AI 助手。能写代码、搜索论文、管理知识库、自我修复崩溃、通过 `/evolve` 自主进化。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入 DEEPSEEK_API_KEY 和 TAVILY_API_KEY
python run.py
```

---

## 核心能力

### 🧬 自进化系统
Agent 能自主修改自己的源代码，通过多层安全网保证不崩溃：

| 组件 | 功能 |
|------|------|
| **覆盖率门禁** | 5 级递进：tools/ 始终可改 → 核心模块需 60-75% 测试覆盖率 |
| **验证流水线** | AST 语法 → import 检查 → pytest → 覆盖率不降 → 无新 lint 错误 |
| **自愈回滚** | 崩溃 → 解析 traceback → 自动回滚肇事文件 → 注入诊断 → 重试 |
| **外部看门狗** | 独立进程监控，心跳超时/进程崩溃 → git reset --hard → 自动重启 |
| **进化档案** | JSONL 记录每次自修改，TF-IDF 搜索历史，避免重复踩坑 |
| `/evolve <目标>` | 触发完整闭环：研究→综合→生成→验证→合入/回滚 |

### 📚 研究知识库（SQLite + RAG 三层检索）

| 层级 | 工具 | 功能 |
|------|------|------|
| L1 浏览 | `rag_browse` | 知识库全貌：标题 + 主题 + 一行摘要 |
| L2 搜索 | `rag_query(scope='research')` | 语义搜索论文、笔记、知识文档 |
| L3 深读 | `rag_read(id)` | 全文内容（最多 8000 字符） |

- **论文搜索**: arXiv + Semantic Scholar，结果自动入库
- **笔记系统**: 关联论文和主题，跨会话持久化
- **知识网络**: 论文间关系（cites/builds_on/contradicts/compares/extends）
- **自动索引**: 搜论文/记笔记后自动更新 RAG 索引

### 💻 编程助手

| 类别 | 工具 |
|------|------|
| 文件 | `read_file`(合并行号模式) `write_file` `edit_file_lines` `undo_edit` `delete_file` `list_files` |
| 代码 | `grep_code` `rag_query(scope='code')` `run_python` `run_command` |
| Git | `git_status` `git_diff` `git_log` `git_commit` `git_push` `git_pr` `git_review` |
| 网络 | `search_web` `read_page`(自动重写 GitHub/arxiv URL) |
| 记忆 | `remember` `recall` `forget` |
| 规划 | `think` |

### ⚡ 性能优化

- **DeepSeek 前缀缓存**: 三段式 CacheContext（不可变 prefix + 只追加 log + 易失 scratch），缓存命中率 90%+
- **SAFE 并行**: 只读工具自动并行执行，有副作用工具串行
- **Storm 去重**: 滑动窗口(64)同轮内拦截重复工具调用
- **预读加速**: 用户输入中的文件路径自动预读，省掉第一轮 API 往返
- **write_file 毫秒级**: import 验证从子进程改为进程内 ast+importlib，9s→<1ms

---

## 快速命令参考

```bash
# 开发
make test          # 运行测试（339 用例）
make lint          # 代码检查（ruff）

# 启动后常用命令
/help              # 指令列表
/context           # 上下文诊断：token分布、缓存、工具定义占比
/stats             # 工具调用统计（频次、成功率、耗时）
/evolve <目标>     # 触发自进化
/watchdog          # 查看外部看门狗状态
/mode auto         # 自动模式（安全操作无需确认）
/model             # 查看/切换 LLM 模型
/clear             # 清除对话上下文
/save              # 强制保存会话
```

---

## 项目结构

```
├── src/
│   ├── main.py              # 主入口 + 崩溃保护 + 预读
│   ├── llm.py               # LLM 后端 + 缓存统计 + SAFE 并行
│   ├── cache_context.py     # 三段式上下文管理器
│   ├── commands.py          # 27+ 命令
│   ├── config.py            # 配置中心
│   ├── session.py           # 会话持久化
│   ├── safety.py            # 三级安全模式
│   ├── guard.py             # 自愈系统（崩溃检测+回滚）
│   ├── watchdog.py          # 外部看门狗进程
│   ├── coverage_gate.py     # 覆盖率渐进门禁（5层）
│   ├── validate.py          # 三阶段验证流水线
│   ├── evolve.py            # 进化档案（JSONL+TF-IDF）
│   ├── evolution_loop.py    # 自进化编排 prompt
│   ├── suggest.py           # 主动建议引擎
│   └── tools/               # 16 个工具模块
│       ├── file.py          #   文件读写/编辑/备份/校验
│       ├── web.py           #   网页搜索/阅读
│       ├── exec.py          #   Python/Shell 执行
│       ├── code.py          #   代码搜索
│       ├── git.py           #   Git 全流程
│       ├── project.py       #   项目扫描/分析
│       ├── lint.py          #   规范检查/文档同步
│       ├── rag.py           #   RAG 索引+语义搜索（三层）
│       ├── evolve.py        #   进化工具（LLM可调用）
│       ├── memory.py        #   记忆系统
│       ├── think.py         #   策略思考
│       ├── storm.py         #   去重引擎
│       ├── tracker.py       #   调用追踪
│       └── __init__.py      #   注册中心+调度
├── tests/                   # 339 测试用例
├── docs/                    # 设计文档
├── memory/                  # 长期记忆
├── knowledge/               # 研究知识（自动被 RAG 索引）
├── AGENTS.md                # AI 智能体操作手册（agent 自我认知）
├── pyproject.toml
└── run.py
```

---

## 相关文档

| 文档 | 读者 | 内容 |
|------|------|------|
| [AGENTS.md](./AGENTS.md) | AI Agent | 完整能力清单、架构规则、安全边界 |
| [docs/architecture.md](./docs/architecture.md) | 开发者 | 系统架构、数据流、设计决策 |
| [docs/cache-design.md](./docs/cache-design.md) | 开发者 | DeepSeek 前缀缓存优化 |
| [docs/development.md](./docs/development.md) | 开发者 | 添加工具/指令/插件的方法 |
| [docs/roadmap.md](./docs/roadmap.md) | 所有人 | 开发路线图 |

---

## 许可证

[MIT](LICENSE)
