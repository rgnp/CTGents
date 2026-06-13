# src/ 模块规模盘点

> 生成时间: 2026-06-13 | 总文件: 48 | 总行数: 16,180

## Top 5 最大文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/llm.py` | 1,578 | LLM 后端抽象：多模型支持、自动路由、流式调用 |
| `src/tools/rag.py` | 1,339 | RAG 检索增强：代码索引 + 语义搜索 |
| `src/tools/lint.py` | 1,142 | 项目规范检查（六维度扫描 + AGENTS.md 生成 + 文档同步检查） |
| `src/tools/git.py` | 973 | Git 操作工具：状态/差异/日志/提交/推送/PR/分支/还原 |
| `src/tools/file.py` | 791 | 文件操作工具：读写、行级编辑、备份与撤销 |

## 完整清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/llm.py` | 1,578 | LLM 后端抽象 |
| `src/tools/rag.py` | 1,339 | RAG 检索增强 |
| `src/tools/lint.py` | 1,142 | 项目规范检查 |
| `src/tools/git.py` | 973 | Git 操作工具 |
| `src/tools/file.py` | 791 | 文件操作工具 |
| `src/commands.py` | 701 | 指令系统（/help, /evolve 等） |
| `src/main.py` | 650 | 主入口：REPL 循环、Esc 监听、会话管理 |
| `src/tools/analyzer.py` | 607 | 静态分析：死代码/圈复杂度/bare except |
| `src/lesson.py` | 555 | 失败模式学习 → 策略记忆 |
| `src/tools/research.py` | 554 | 科研工具：论文扫描、批量读取、评分归档 |
| `src/tools/project.py` | 521 | 项目结构扫描：语言/框架/依赖检测 |
| `src/tools/memory.py` | 460 | 记忆系统：remember/recall/forget |
| `src/tools/evolve.py` | 435 | 进化系统工具：查询/验证/状态 |
| `src/tools/exec.py` | 427 | 命令执行：run_python/run_command/run_async/poll |
| `src/tools/self.py` | 407 | 自我认知系统：架构知识 + 运行时数据 |
| `src/validate.py` | 381 | 三阶段验证：AST → pytest → 覆盖率/lint |
| `src/evolution_runner.py` | 362 | 进化运行器：持久化 run/state/patch |
| `src/tools/web.py` | 358 | Web 工具：search_web + read_page，带 TTL 缓存 |
| `src/gaps.py` | 327 | 知识缺口管理 |
| `src/diagnostics.py` | 325 | 诊断层：tracker 异常 → 可行动诊断 |
| `src/tracker.py` | 314 | 工具调用性能追踪（被动进化感知层唯一数据入口） |
| `src/tasks.py` | 304 | 长任务状态：current.md 读取/判活/注入/归档 |
| `src/session_summary.py` | 287 | 会话摘要生成 |
| `src/tools/repo.py` | 287 | 仓库管理：clone/list/status |
| `src/evolve.py` | 285 | 进化档案（JSONL）读写/查询 |
| `src/cache_context.py` | 225 | 三段式上下文管理器：prefix/log/scratch |
| `src/tools/__init__.py` | 210 | 工具注册/调度/热加载入口 |
| `src/params.py` | 177 | 集中可调旋钮（frozen dataclass，按域分组） |
| `src/outcome.py` | 176 | 任务闭环：目标→执行→评分→修订 |
| `src/tools/_tool_meta.py` | 163 | 工具元数据派生（唯一真相源） |
| `src/tools/tool_guard.py` | 162 | 工具调用边界防护 |
| `src/tools/storm.py` | 157 | 工具调用去重 + 结果缓存 |
| `src/citation_audit.py` | 128 | 引用取证审计 |
| `src/tools/paper.py` | 120 | 论文分析/卡片生成 |
| `src/session_pins.py` | 119 | 会话钉板 |
| `src/config.py` | 108 | 配置中心：密钥/模型/路径 |
| `src/tools/learn.py` | 100 | learn 工具 |
| `src/task_loop.py` | 88 | 长任务自主续跑驱动 |
| `src/completion_audit.py` | 86 | 收尾取证自检 |
| `src/session.py` | 85 | 会话持久化存储 |
| `src/tools/tokens.py` | 81 | Token 计数工具 |
| `src/guard.py` | 69 | 自我修改分级表（三层安全模型） |
| `src/tools/code.py` | 68 | 代码分析工具 |
| `src/gate_audit.py` | 62 | 覆盖率门禁审计 |
| `src/tools/pin.py` | 58 | pin/unpin 工具实现 |
| `src/tools/analyzer_tool.py` | 50 | analyze_code 工具 |
| `src/tools/think.py` | 34 | think 工具 |
| `src/__init__.py` | 0 | 包标记（空文件） |

## 分布统计

| 分类 | 文件数 | 总行数 | 占比 |
|------|--------|--------|------|
| src/ 根模块 | 21 | 5,199 | 32% |
| src/tools/ | 25 | 8,569 | 53% |
| 1000+ 行 | 3 | — | — |
| 500-999 行 | 8 | — | — |
| 100-499 行 | 24 | — | — |
| <100 行 | 13 | — | — |
