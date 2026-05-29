# 开发路线图：从通用 Agent → 终端编程助手

> 目标：以当前 Agent 框架为基础，开发出一个媲美 Claude Code 的终端 AI 编程助手，
> 摆脱单一模型的绑定和约束，自由选择模型后端。

---

## 现状评估

当前 Agent 已有的能力：

| 维度 | 能力 |
|------|------|
| ✅ 对话 | 多轮对话、流式输出、上下文管理、Token 预算 |
| ✅ 工具系统 | 工具注册/调度、插件热加载、Tool Call 循环 |
| ✅ 文件操作 | read_file / write_file / list_files / delete_file |
| ✅ 代码搜索 | grep_code（正则搜索） |
| ✅ 代码执行 | run_python（子进程隔离 + 超时） |
| ✅ 网络 | search_web / read_page（Tavily + trafilatura） |
| ✅ 思考 | think 工具（策略规划） |
| ✅ 记忆 | remember / recall / forget（跨会话持久化） |
| ✅ 插件 | 自学安装、热加载、COMMANDS 注册 |
| ✅ Skill | 技能包管理（加载/卸载/自动匹配） |
| ✅ 会话 | 保存/恢复/重命名/导出/列表 |
| ✅ 指令系统 | 结构化注册、热加载、/help 自动聚合 |

当前 Agent **缺乏**的关键能力：

| 维度 | 缺失 |
|------|------|
| ❌ Shell 执行 | 只能跑 Python，不能跑 npm/pip/git/make 等 |
| ❌ 文件编辑 | 只有覆写式 write_file，没有行级增删改 |
| ❌ Git 集成 | 无 git 工具 |
| ❌ 项目感知 | 不自动分析项目结构、语言、依赖 |
| ❌ 多模型 | 硬编码 DeepSeek，不支持按任务路由不同模型 |
| ❌ Auto Mode | 所有工具调用都需要 LLM 判断，无安全等级机制 |
| ❌ 长任务 | 无后台会话、无目标驱动持续执行 |
| ❌ MCP | 无 MCP 服务器支持 |

---

## 第一阶段：核心能力补齐（近期 · 1-2 周）

### 1.1 通用 Shell 执行工具

- [ ] 将 `run_python` 扩展为通用 `run_command`，支持任意 shell 命令
- [ ] 安全机制：白名单/黑名单命令、超时控制、工作目录隔离
- [ ] 实时输出流式显示（类似 Claude Code 的终端输出）
- [ ] 后台命令执行（不阻塞对话，可查状态）

**涉及文件：** `src/tools/exec.py`

### 1.2 文件行级编辑工具

- [ ] `edit_file` — 替换指定行范围
- [ ] `insert_line` — 在某行后插入
- [ ] `patch_file` — 按 diff 格式批量修改
- [ ] 修改前自动备份、修改后可撤销

**涉及文件：** `src/tools/file.py`

### 1.3 Git 操作工具

- [ ] `git_status` — 查看工作区状态
- [ ] `git_diff` — 查看未暂存/已暂存变更
- [ ] `git_commit` — 自动生成 commit message 并提交
- [ ] `git_push` — 推送
- [ ] `git_pr` — 创建 Pull Request
- [ ] `git_log` — 查看提交历史

**涉及文件：** `src/tools/git.py`（新建）

### 1.4 项目结构感知

- [ ] 启动时自动扫描项目目录，生成项目结构树
- [ ] 识别项目语言（检测 package.json / pyproject.toml / Cargo.toml 等）
- [ ] 识别构建命令（npm test / pytest / cargo build 等）
- [ ] 将项目上下文注入 system prompt

**涉及文件：** `src/tools/project.py`（新建）、`src/main.py`

---

## 第二阶段：工作流深度（中期 · 3-4 周）

### 2.1 多模型后端与路由

- [ ] 抽象 LLM 后端接口（`LLMBackend` 类），支持切换：
  - DeepSeek V3/V4
  - Claude（Sonnet/Opus）
  - GLM / Qwen / MiniMax 等国产模型
- [ ] 简单任务 / 复杂任务路由（按 Token 预算或任务复杂度自动选择模型）
- [ ] `/model` 命令：运行时切换模型（类似 Claude Code）

**涉及文件：** `src/llm.py`、`src/config.py`

### 2.2 Auto Mode（安全自动执行）

- [ ] 给每个工具标注安全等级：
  - `safe` — 自动放行（读文件、搜索等）
  - `risky` — 暂停确认（写文件、删除等）
  - `dangerous` — 必须确认（删除目录、推送等）
- [ ] 可配置的安全策略（允许/拒绝/询问）
- [ ] 会话内记住用户选择（例如"本次会话允许 git push"）

**涉及文件：** `src/tools/__init__.py`、`src/main.py` 新增安全模块

### 2.3 目标驱动长任务

- [ ] `/goal` 命令：设定完成条件，Agent 自主持续执行直到达成
- [ ] Agent 循环中自动检查目标是否达成
- [ ] 失败自动重试、变更自动 commit
- [ ] 后台会话：`claude --bg` 在后台运行任务

**涉及文件：** `src/commands.py`、`src/llm.py` 新增长任务循环

### 2.4 项目级记忆

- [ ] 跨会话记住项目的：
  - 构建命令和测试命令
  - 代码风格偏好
  - 常用工具链
  - 踩过的坑
- [ ] 存储为项目本地 `.agent-memory/` 目录，随 Git 版本控制

**涉及文件：** `src/tools/memory.py`

---

## 第三阶段：平台化（远期 · 1-2 月）

### 3.1 MCP 协议支持

- [ ] 支持 Model Context Protocol（MCP）服务器接入
- [ ] 支持 stdio / HTTP / SSE 三种传输类型
- [ ] 可接入外部数据源（数据库、文件系统、API 网关等）
- [ ] MCP 工具自动注册为 Agent 可用工具

### 3.2 Skill 自动匹配

- [ ] Agent 启动时分析当前任务上下文
- [ ] 自动加载最匹配的 Skill（已完成 skill_auto_match 接口）
- [ ] 任务完成后自动卸载 Skill

### 3.3 IDE/编辑器集成

- [ ] VS Code 扩展：在编辑器内对话，高亮代码片段
- [ ] 行内建议（类似 Copilot）
- [ ] 文件差异对比（修改前/修改后）

### 3.4 架构自进化

- [ ] Agent 能识别自己的性能瓶颈并生成改进提案
- [ ] 工具调用热替换（不停机更新工具实现）
- [ ] Agent 间协作（多 Agent 分布式执行）

---

## 版本里程碑

| 版本 | 目标 | 预计 |
|------|------|------|
| v0.1 | 现状：通用对话 Agent | ✅ 已完成 |
| **v0.2** | Shell 执行 + 文件行级编辑 + Git 操作 | 近期 |
| **v0.3** | 多模型路由 + Auto Mode + 项目感知 | 中期 |
| **v0.4** | 目标驱动长任务 + 项目记忆 | 中期 |
| **v1.0** | MCP 支持 + Skill 自动匹配 + IDE 集成 | 远期 |

---

## 技术债务与架构优化

- [ ] **配置集中化**：目前的 `.env` + `settings.json` + 环境变量三者混杂，需要统一管理
- [ ] **错误恢复**：工具调用失败时提供更友好的回退路径（当前重试机制已不错，可补充异常分类）
- [ ] **测试覆盖**：为 tools 模块写单元测试，特别是文件操作和 git 操作
- [ ] **插件接口文档化**：当前 `PLUGIN_SPEC` 只有 TOOLS + execute + DESCRIPTION，需要补充监听器接口
- [ ] **日志系统**：当前用 logging，可补充详细的调用链路追踪

---

## 设计原则

1. **模型无关**：核心逻辑不依赖任何特定模型，LLM 是可插拔的后端
2. **安全优先**：所有自动化操作必须有安全护栏，用户始终有最终控制权
3. **渐进增强**：每阶段的能力独立可运行，不等待全部完成再发布
4. **可观测**：Agent 做的每件事、花的每个 token、调用的每个工具都可查
