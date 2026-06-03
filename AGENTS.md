# AGENTS.md — AI 编程智能体操作手册

> 本文档是你在本项目中的**完整能力清单**。每次会话启动时会被加载。
> 阅读本文档后，你应该清楚自己能做什么、有哪些工具、遵循什么规则。

---

## 快速参考：所有命令

| 命令 | 用途 |
|------|------|
| `/help` | 指令列表 |
| `/context` | 上下文诊断：token分布、缓存、工具定义占比 |
| `/stats` | 工具调用统计（频次、成功率、耗时） |
| `/clear` | 清除对话上下文 |
| `/compact [keep=N]` | 手动压缩对话历史 |
| `/new` | 新建会话 |
| `/save` | 强制保存 |
| `/load <编号>` | 切换会话 |
| `/sessions` | 列出历史会话 |
| `/rename <名称>` | 重命名会话 |
| `/delete <编号>` | 删除会话 |
| `/export [轮数] [文件名]` | 导出对话为 Markdown |
| `/pop [数量]` | 撤回最后 N 条对话 |
| `/model [flash\|pro]` | 切换模型 |
| `/mode [manual\|auto]` | 安全模式切换 |
| `/reload` | 热加载工具和指令 |
| `/self` | 自省：查看架构全景 |
| **`/evolve <目标>`** | **触发自进化：研究→综合→生成→验证→合入** |
| **`/research <主题>`** | **纯研究模式：多源搜索+模式提取，不改代码** |
| **`/watchdog`** | **查看外部看门狗状态** |

---

## 完整工具清单（按类别）

### 📁 文件操作
| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件。不传行号=全文，传 start_line/end_line=指定范围带行号 |
| `write_file` | 创建/覆写文件。自动备份+语法校验+import 检查。受保护文件拒绝修改 |
| `edit_file_lines` | 行级编辑（replace/insert/delete）。自动备份+校验 |
| `undo_edit` | 撤销最近一次编辑（从备份恢复） |
| `delete_file` | 删除文件 |
| `list_files` | 浏览目录 |
| `count_lines` | 统计文件行数 |

### 🔍 代码搜索
| 工具 | 说明 |
|------|------|
| `grep_code` | 正则搜索代码内容 |
| `rag_query` | RAG 语义搜索代码 |
| `rag_index` | 重建 RAG 索引 |
| `rag_status` | RAG 索引状态 |

### 🌐 网络搜索
| 工具 | 说明 |
|------|------|
| `search_web` | 互联网搜索（Tavily），5分钟缓存 |
| `read_page` | 读取网页全文，10分钟缓存。自动重写 GitHub/arxiv URL |

### 📚 研究知识库（SQLite）
| 工具 | 说明 |
|------|------|
| `search_papers` | 搜索论文（arXiv + Semantic Scholar），**结果自动入库** |
| `read_paper` | 读论文详情，**自动记录阅读** |
| `save_note` | 保存研究笔记，可关联论文和主题 |
| `search_knowledge` | 搜索知识库（论文+笔记），支持主题/年份筛选 |
| `kb_topics` | 查看知识库主题分类树 |
| `link_papers` | 建立论文间关系（cites/builds_on/contradicts/compares/extends） |
| `kb_stats` | 知识库统计：论文数、笔记数、阅读进度 |

### 💻 代码执行
| 工具 | 说明 |
|------|------|
| `run_python` | 执行 Python 代码（子进程隔离） |
| `run_command` | 执行 Shell 命令。危险命令自动拦截 |

### 📦 Git
| 工具 | 说明 |
|------|------|
| `git_status` | 工作区状态 |
| `git_diff` | 文件变更详情 |
| `git_log` | 提交历史 |
| `git_branch` | 分支列表 |
| `git_review` | 审查暂存变更（检查类型注解、异常处理、死代码等） |
| `git_commit` | 暂存并提交（message 不传则自动生成） |
| `git_push` | 推送到远程 |
| `git_pr` | 创建 Pull Request |

### 🧠 记忆系统
| 工具 | 说明 |
|------|------|
| `remember` | 记住信息（持久化到 memory/） |
| `recall` | 回忆记忆 |
| `forget` | 删除记忆 |

### 🔌 插件 & MCP
| 工具 | 说明 |
|------|------|
| `install_plugin` | 安装插件（写入 plugins/ 并热加载） |
| `list_plugins` | 列出已安装插件 |
| `plugin_spec` | 获取插件接口规范 |
| `discover` | 扫描所有可用能力 |
| `mcp_connect` | 连接 MCP 服务器 |
| `mcp_disconnect` | 断开 MCP 连接 |
| `mcp_list` | 列出 MCP 服务器 |

### 🧬 自进化系统
| 工具 | 说明 |
|------|------|
| `evolve_query` | 查询进化档案（过去的自修改记录，含失败教训） |
| `evolve_check_access` | 检查文件修改权限（覆盖率门禁） |
| `evolve_coverage` | 查看覆盖率报告和可修改文件列表 |
| `evolve_validate` | 运行验证流水线（静态检查→pytest→覆盖率） |
| `evolve_suggest_tests` | 获取解锁修改权限的测试建议 |
| `evolve_status` | 进化系统状态总览 |

### 📋 项目 & 规范
| 工具 | 说明 |
|------|------|
| `scan_project` | 扫描项目结构 |
| `check_project` | 六维度规范检查（评分 0-100） |
| `generate_agents_md` | 生成/更新 AGENTS.md |
| `docs_sync_check` | 检查代码变更是否同步了文档 |
| `subagent` | 创建只读子代理执行独立任务 |
| `think` | 策略规划：拆解问题、评估信息完整性 |

---

## 核心架构规则

### 1. 缓存优先（最重要）
- 上下文分三段：**Immutable Prefix** → **Append-Only Log** → **Volatile Scratch**
- **绝不修改 log 中的任何已有消息**，只能追加
- 系统消息全部放在末尾，不影响前缀缓存
- 自动压缩已关闭（99% 才触发），不要手动触发不必要的压缩
- 工具结果超过 800 字符自动截断，标注"可重新读取"

### 2. 性能规则
- **先规划再执行**：需要读多个文件时一次性列出，系统会并行读取
- 修改代码时先改完所有文件，最后统一验证
- 只读工具（read_file、grep_code、search_web 等）自动并行执行
- 有副作用工具（write_file、run_command 等）串行执行
- 同轮内相同工具+相同参数自动去重（Storm 去重）

### 3. 安全规则
- 三级安全模式：SAFE（直接执行）/ RISKY（确认后执行）/ DANGEROUS（禁止）
- 修改代码前自动 git commit 快照
- 语法错误和 import 错误在写入时自动拦截+回滚
- 运行时崩溃自动分析 traceback → 回滚肇事文件 → 注入诊断上下文 → 重试
- 外部看门狗进程监控 agent 健康，崩溃后自动 git reset + 重启

### 4. 自进化规则
- `/evolve` 触发完整闭环：研究→综合→生成→验证→合入/回滚
- 修改代码必须通过验证流水线（静态检查 + pytest + 覆盖率不降）
- 覆盖率门禁：tools/ 始终可改，核心模块需要 45-75% 覆盖率
- guard.py 和 watchdog.py 不可修改
- 每次自修改记录到进化档案，下次查询避免重复踩坑

### 5. 研究规则
- 论文搜索结果自动存入 SQLite 知识库
- 读论文自动记录阅读历史
- 笔记可关联论文和主题，跨会话持久化
- 用 `search_knowledge` 检索已有知识，避免重复搜索

---

## 技术栈

- Python 3.12+
- DeepSeek V4 Flash/Pro（自动路由）
- SQLite（研究知识库）
- pytest（295 测试用例）

## 常用命令

```
pytest              # 运行所有测试
pytest tests/xxx.py # 运行单个测试文件
ruff check src/     # lint 检查
ruff format src/    # 格式化代码
```
