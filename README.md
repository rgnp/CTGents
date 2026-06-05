# CTGents — 自进化 AI 编程助手

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/rgnp/CTGents)](LICENSE)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek_V4-green)](https://deepseek.com)

终端里的自进化 AI 编程助手。基于 DeepSeek V4，能写代码、搜索网络、管理知识库、通过 `/evolve` 自主进化。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入 DEEPSEEK_API_KEY
python run.py
```

---

## 核心能力

### 自进化系统

Agent 能自主研究、修改自己的源代码，通过多层验证保证不崩溃：

| 组件 | 功能 |
|------|------|
| **覆盖率门禁** | 4 级递进：tools/ 0% → config 45% → core 60% → critical 75% |
| **验证流水线** | AST 语法 → pytest → 覆盖率不降 → 无新 lint 错误 |
| **进化档案** | JSONL 记录每次自修改，TF-IDF 搜索历史，避免重复踩坑 |
| `/evolve <目标>` | 触发完整闭环：研究 → 综合 → 生成 → 验证 → 合入/回滚 |

### RAG 语义搜索

| 工具 | 功能 |
|------|------|
| `rag_index` | 索引项目代码库（30+ 语言），增量更新 |
| `rag_query` | 语义搜索，scope='code' 搜代码，'research' 搜知识库 |
| `rag_status` | 查看索引状态 |

### 编程工具

| 类别 | 工具 |
|------|------|
| 文件 | `read_file` `write_file` `edit_file_lines` `delete_file` `list_files` `count_lines` |
| 代码 | `grep_code` `run_python` `run_command` |
| Git | `git_status` `git_diff` `git_log` `git_review` `git_commit` `git_push` `git_pr` `git_branch` |
| 网络 | `search_web` `read_page` |
| 记忆 | `remember` `recall` `forget` |
| 思考 | `think` |
| 自我 | `self` — 查看自己的完整架构和能力 |

### 性能

- **DeepSeek 前缀缓存**: 三段式 CacheContext（不可变 prefix + 只追加 log + 易失 scratch）
- **并行执行**: 只读工具自动并行（ThreadPoolExecutor），有副作用工具串行
- **Storm 去重**: 滑动窗口拦截同轮重复工具调用
- **预读加速**: 用户输入中的文件路径自动预读到上下文

---

## 启动后常用命令

```
/help              # 指令列表
/context           # 上下文诊断：token 分布、缓存命中
/evolve <目标>     # 触发自进化
/model             # 查看/切换 LLM 模型
/self              # 查看自身架构和能力
/clear             # 清除对话上下文
/save              # 保存会话
/load <id>         # 加载历史会话
/sessions          # 列出所有会话
```

---

## 项目结构

```
src/
  main.py              # 主入口 + Esc 中断 + 预读优化
  llm.py               # LLM 后端 + 并行执行 + 缓存统计
  cache_context.py     # 三段式上下文管理器
  commands.py          # 指令系统（/help /save /evolve 等）
  config.py            # 配置中心
  session.py           # 会话持久化
  guard.py             # 自我保护（is_protected 阻止修改 guard.py）
  coverage_gate.py     # 覆盖率门禁（4 tier 渐进解锁）
  validate.py          # 三阶段验证流水线（AST→pytest→覆盖率）
  evolve.py            # 进化档案（JSONL + TF-IDF 查询）
  evolution_loop.py    # 进化编排器
  suggest.py           # 主动建议引擎
  tools/
    __init__.py        # 注册中心 + 调度 + 热加载
    file.py            # 文件读写/编辑
    web.py             # 网页搜索/阅读
    exec.py            # Python/Shell 执行
    code.py            # 代码搜索
    git.py             # Git 全流程
    project.py         # 项目扫描/分析
    lint.py            # 规范检查/文档同步
    rag.py             # RAG 索引 + 语义搜索
    evolve.py          # 进化工具（LLM 可调用）
    memory.py          # 记忆系统
    think.py           # 策略思考
    self.py            # 自我认知
    storm.py           # 去重引擎
    tracker.py         # 调用追踪
    tokens.py          # Token 计数
    reflect.py         # 失败反思
tests/                 # 291 个测试用例
docs/                  # 设计文档
memory/                # 长期记忆
knowledge/             # 研究知识库
AGENTS.md              # AI Agent 操作手册
```

---

## 开发

```bash
make test           # 全部测试
make lint           # ruff 检查
make lint-fix       # 自动修复
make preflight      # lint + test + docs-sync + check
```

## 许可证

[MIT](LICENSE)
