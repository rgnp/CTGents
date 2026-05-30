# 开发路线图：从通用 Agent → 终端编程助手

> 目标：以当前 Agent 框架为基础，开发出一个媲美 Claude Code 的终端 AI 编程助手，
> 摆脱单一模型的绑定和约束，自由选择模型后端。

---

## 最新更新（2026-05-30 v0.8 — 🚧 进行中）

### 1.9 DeepSeek 前缀缓存优化（基于 Reasonix 调研）

> 目标：将会话上下文改为三段式结构（Immutable Prefix / Append-Only Log / Volatile Scratch），
> 把 DeepSeek 前缀缓存命中率从 ~0% 提升到 90%+，大幅降低长期会话的 token 费用。

- [x] 三段式上下文：`prefix`（固定）+ `log`（只追加）+ `scratch`（不发给 API）
- [x] 修复 `main.py` 的 `insert(0, ...)` 缓存毒药问题
- [x] 工具结果超过 3000 token 自动压缩为摘要
- [ ] SAFE 工具并行分发（read_file、git_status 等）
- [ ] Flatten：深度嵌套工具 schema 自动扁平化
- [ ] Storm：相同 tool+args 滑动窗口去重

---

## v0.9 规划 — 多后端 + 体验增强

### 2.0 多 LLM 后端抽象

> 当前仅支持 DeepSeek，扩展后将支持任意兼容 OpenAI API 的模型。

- [ ] `LLMBackend` 抽象层完善：统一 streaming / 工具调用 / 错误处理接口
- [ ] OpenAI 后端（GPT-4o / o3 / o4-mini）
- [ ] Anthropic 后端（Claude Sonnet 4 / Opus）
- [ ] Ollama 后端（本地模型，零成本调试）
- [ ] 多后端自动路由：按任务类型 / 成本 / 可用性自动选择
- [ ] `/model` 命令升级：支持切换后端 + 模型组合

**涉及文件：** `src/llm.py`、`src/config.py`

### 2.1 终端体验升级

- [ ] `run_command` 流式实时输出（类似 Claude Code 终端效果）
- [ ] `run_command` 后台运行（不阻塞对话，`/jobs` 查看状态）
- [ ] 多 Tab / 分屏支持（对话 + 文件 + 终端）

**涉及文件：** `src/tools/exec.py`、`src/main.py`

---

## v1.0 规划 — 智能 + 平台化

### 3.1 MCP 协议支持

> [Model Context Protocol](https://modelcontextprotocol.io) 是 AI Agent 的标准化工具协议。
> 接入后 CTGents 可连接任何 MCP 服务器——数据库、文件系统、浏览器、第三方 API。

- [ ] MCP Client 实现（支持 stdio / HTTP / SSE 传输）
- [ ] MCP 工具自动注册为 Agent 可用工具
- [ ] MCP 服务器配置管理（`~/.ctgents/mcp.json`）
- [ ] 内置 MCP 服务器示例（文件系统 + 搜索）

**涉及文件：** `src/tools/mcp.py`（新模块）

### 3.2 项目级 RAG 检索

> 当前记忆系统是手动 `remember`/`recall`。加上自动索引——启动时扫描项目文件，
> embedding 存入向量库，对话中自动检索相关代码上下文。

- [ ] 项目文件自动索引（支持 Python / JS / TS / Go / Rust 等）
- [ ] 轻量级向量存储（chromadb / local 文件）
- [ ] 对话中自动检索：检测到代码相关问题时自动召回上下文
- [ ] 索引增量更新（文件变更时重新索引）
- [ ] `.agent-memory/` 目录随 Git 版本控制

**涉及文件：** `src/tools/memory.py`、`src/tools/rag.py`（新模块）

### 3.3 目标驱动长任务

- [ ] `/goal` 命令：设定完成条件，Agent 自主持续执行直到达成
- [ ] Agent 循环中自动检查目标是否达成
- [ ] 失败自动重试、变更自动 commit

**涉及文件：** `src/commands.py`、`src/llm.py`

---

## 远期规划

### 4.1 Web UI

> 轻量级 Web 界面，方便手机 / 平板 / 远程使用。

- [ ] FastAPI + HTMX（前后端一体，零 JS 构建）
- [ ] 流式响应输出
- [ ] 会话管理（保存 / 加载 / 历史）
- [ ] 文件浏览 + 差异对比视图

### 4.2 IDE 集成

- [ ] VS Code 扩展：在编辑器内对话，高亮代码片段
- [ ] GitHub Copilot 扩展协议对接

### 4.3 架构自进化

- [ ] Agent 能识别自己的性能瓶颈并生成改进提案
- [ ] 工具调用热替换（不停机更新工具实现）

---

## 现状评估

### 已有能力

| 维度 | 状态 |
|------|:----:|
| 会话保存 / 恢复 / 重命名 / 导出 / 列表 | ✅ |
| 指令系统（结构化注册、热加载、/help 自动聚合） | ✅ |
| Flash/Pro 双模型自动路由、/model 切换 | ✅ |
| `/reload` 热加载（指令 + 工具 + 插件） | ✅ |
| 文件操作（read_file / write_file / list_files / delete_file） | ✅ |
| 文件行级编辑（read_file_lines / edit_file_lines / undo_edit） | ✅ |
| Shell 执行（run_command + run_python，安全黑名单） | ✅ |
| 代码搜索（grep_code 正则搜索） | ✅ |
| 网络（search_web / read_page，Tavily + trafilatura） | ✅ |
| think 工具（策略规划） | ✅ |
| 记忆系统（remember / recall / forget，跨会话持久化） | ✅ |
| 插件系统（自学安装、热加载、COMMANDS 注册） | ✅ |
| Skill 技能包（加载 / 卸载 / 自动匹配） | ✅ |
| 项目感知（scan_project，20+ 语言框架自动检测） | ✅ |
| 规范检查（check_project 六维度评分 + AGENTS.md 自动生成） | ✅ |
| Git 集成（status / diff / commit / push / PR / log / branch） | ✅ |
| Auto Mode（三级安全 + 手动/自动 + 会话信任） | ✅ |
| 文档同步强制（docs_sync_check + CI + pre-commit） | ✅ |
| 文档体系（架构 / 开发 / Changelog / 贡献指南 / Skills） | ✅ |
| DeepSeek 前缀缓存（三段式上下文） | ✅ |

### 待建设能力

| 维度 | 优先级 | 预计版本 |
|------|:------:|:--------:|
| 多 LLM 后端（OpenAI / Claude / Ollama） | 🔴 高 | v0.9 |
| 终端流式输出 + 后台命令 | 🔴 高 | v0.9 |
| MCP 协议支持 | 🟡 中 | v1.0 |
| 项目级 RAG 检索 | 🟡 中 | v1.0 |
| 目标驱动长任务 | 🟡 中 | v1.0 |
| Web UI | 🟢 低 | 远期 |
| IDE 集成 | 🟢 低 | 远期 |
| 架构自进化 | 🟢 低 | 远期 |

---

## 版本里程碑

| 版本 | 目标 | 状态 |
|:----:|------|:----:|
| v0.4 | 多模型路由（Flash/Pro） | ✅ |
| v0.5 | 项目规范检查 + AGENTS.md + 测试骨架 | ✅ |
| v0.6 | Auto Mode 安全系统 | ✅ |
| v0.7 | 文档同步强制 + 文档体系 + CI | ✅ |
| v0.8 | DeepSeek 前缀缓存优化 | 🚧 进行中 |
| v0.9 | 多 LLM 后端 + 终端体验增强 | 🗓️ 规划中 |
| v1.0 | MCP + RAG + 长任务 | 🗓️ 规划中 |
| v1.x | Web UI + IDE 集成 + 自进化 | 🗓️ 远期 |

---

## 技术债务

- [ ] **实时输出**：`run_command` 支持流式输出，类似 Claude Code 终端效果
- [ ] **后台命令**：`run_command` 支持后台运行，不阻塞对话
- [ ] **配置集中化**：`.env` + 环境变量 + 常量的统一管理
- [ ] **日志系统**：补充详细的调用链路追踪
- [ ] **错误恢复**：工具调用失败时更友好的回退路径

---

## 设计原则

1. **模型无关**：核心逻辑不依赖任何特定模型，LLM 是可插拔的后端
2. **安全优先**：所有自动化操作必须有安全护栏，用户始终有最终控制权
3. **文档同步**：写代码只是完成了一半，同步更新文档才算完成
4. **渐进增强**：每阶段的能力独立可运行，不等待全部完成再发布
5. **可观测**：Agent 做的每件事、花的每个 token、调用的每个工具都可查
