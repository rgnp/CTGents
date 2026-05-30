# 开发路线图：从通用 Agent → 终端编程助手

> 目标：以当前 Agent 框架为基础，开发出一个媲美 Claude Code 的终端 AI 编程助手，
> 摆脱单一模型的绑定和约束，自由选择模型后端。

---

## 最新更新（2026-05-30 v0.8 — 🚧 进行中）

### 1.9 DeepSeek 前缀缓存优化（基于 Reasonix 调研）

> 目标：将会话上下文改为三段式结构（Immutable Prefix / Append-Only Log / Volatile Scratch），
> 把 DeepSeek 前缀缓存命中率从 ~0% 提升到 90%+，大幅降低长期会话的 token 费用。

- [ ] 三段式上下文：`prefix`（固定）+ `log`（只追加）+ `scratch`（不发给 API）
- [ ] 修复 `main.py` 的 `insert(0, ...)` 缓存毒药问题
- [ ] 工具结果超过 3000 token 自动压缩为摘要
- [ ] SAFE 工具并行分发（read_file、git_status 等）
- [ ] Flatten：深度嵌套工具 schema 自动扁平化
- [ ] Storm：相同 tool+args 滑动窗口去重

**设计文档：** `docs/cache-design.md`

---



## 最新更新（2026-05-29 v0.7）

### 1.7 文档同步强制机制 ✅

- [x] `docs_sync_check` 工具：硬编码 _DOC_SYNC_MAP 映射表，改代码自动检查是否漏了文档
- [x] CI 中新增 docs-sync job，违规阻止 PR 合并
- [x] pre-commit 本地钩子，commit 前自动检查
- [x] Makefile 新增 `make docs-sync` / `make preflight`
- [x] AGENTS.md + docs/development.md 记录完整映射表和提交流程

**涉及文件：** `src/tools/lint.py`、`.pre-commit-config.yaml`、`.github/workflows/ci.yml`

### 1.8 根目录清理 + 文档全面整改 ✅

- [x] 文档审计：发现并修复 6 个问题
- [x] 创建 `docs/architecture.md`、`docs/development.md`、`docs/skills.md`
- [x] CHANGELOG/CONTRIBUTING/FEATURES/ROADMAP 移入 docs/
- [x] 删除根目录残留 `learn_skill.md`、`__init__.py`
- [x] GitHub Actions CI：`make check` 评分低于 80 分报错

**涉及文件：** `docs/`（7 个文档文件）、`README.md`、`AGENTS.md`

---

## 现状评估

当前 Agent 已有的能力：

| 维度 | 能力 |
|------|------|
| ✅ 会话 | 保存/恢复/重命名/导出/列表 |
| ✅ 指令系统 | 结构化注册、热加载、/help 自动聚合、/mode /trust |
| ✅ 多模型 | Flash/Pro 双模型自动路由、/model 切换 |
| ✅ 热加载 | `/reload` 刷新指令系统 + 内置工具 + 插件，无需重启 |
| ✅ 文件操作 | read_file / write_file / list_files / delete_file |
| ✅ 文件行级编辑 | read_file_lines（带行号读取）、edit_file_lines（替换/删除/插入）、undo_edit（撤销） |
| ✅ Shell 执行 | run_command + run_python，含安全黑名单 |
| ✅ 代码搜索 | grep_code（正则搜索） |
| ✅ 网络 | search_web / read_page（Tavily + trafilatura） |
| ✅ 思考 | think 工具（策略规划） |
| ✅ 记忆 | remember / recall / forget（跨会话持久化） |
| ✅ 插件 | 自学安装、热加载、COMMANDS 注册 |
| ✅ Skill | 技能包管理（加载/卸载/自动匹配） |
| ✅ 项目感知 | scan_project 自动检测语言/框架/依赖 |
| ✅ 规范检查 | check_project 六维度评分 + AGENTS.md 自动生成 |
| ✅ Git 集成 | status/diff/commit/push/PR/log/branch 全套 |
| ✅ Auto Mode | 三级安全等级 + 手动/自动模式 + 会话信任 |
| ✅ 文档同步强制 | docs_sync_check + CI + pre-commit 三重闸门 |
| ✅ 文档体系 | 架构/开发/Changelog/贡献指南/Skills 完整 |

当前 Agent **缺乏**的关键能力：

| 维度 | 能力 |
|------|------|
| ❌ 长任务 | 无后台会话、无目标驱动持续执行 |
| ❌ 多厂商 | 只有 DeepSeek，无 Claude/GPT/Qwen 后端 |
| ❌ MCP | 无 MCP 服务器支持 |
| ❌ 实时输出 | run_command 无流式输出（类 Claude Code 终端效果） |
| ❌ 后台命令 | run_command 不支持后台运行 |

| ❌ Token 缓存 | DeepSeek 前缀缓存命中率 ~0%，长期会话浪费大量费用 |
| ❌ 长任务 | 无后台会话、无目标驱动持续执行 |
| ❌ 多厂商 | 只有 DeepSeek，无 Claude/GPT/Qwen 后端 |
| ❌ MCP | 无 MCP 服务器支持 |
### ~~1.1 通用 Shell 执行工具~~ ✅
- [x] `run_command` + `run_python` 安全黑名单 + 超时控制
- [ ] **待办**：实时输出流式显示（类似 Claude Code 的终端效果）
- [ ] **待办**：后台命令执行（不阻塞对话，可查状态）

### ~~1.2 文件行级编辑工具~~ ✅
- [x] read_file_lines / edit_file_lines / undo_edit 三件套
- [x] 自动备份 + Python 语法校验 + 自动回滚

### ~~1.3 Git 操作工具~~ ✅
- [x] git_status / git_diff / git_commit / git_push / git_pr / git_log / git_branch

### ~~1.4 项目结构感知~~ ✅
- [x] scan_project：20+ 语言/框架自动检测 + 文件树 + 依赖概览

### ~~2.1 多模型后端与路由~~ ✅
- [x] LLMBackend 抽象基类 + DeepSeekBackend 实现
- [x] Flash/Pro 双模型 + 任务复杂度自动路由
- [x] `/model` 命令切换
- [ ] **待办**：Claude / GPT / Qwen 等其他厂商接入
- [ ] **待办**：混合调用（思考用 Pro，执行用 Flash）

---

## 第二阶段：工程化增强 ✅（已完成）

### ~~2.2 Auto Mode 安全系统~~ ✅
- [x] 三级安全等级：SAFE（只读）/ RISKY（可逆）/ DANGEROUS（破坏性）
- [x] 29 个内置工具安全等级标注
- [x] 手动/自动双模式（`/mode` 命令）
- [x] 会话信任机制（`/trust` 命令）
- [x] 工具执行前交互式确认
- [x] 安全模式信息注入 system prompt

### ~~1.5 项目规范检查~~ ✅
- [x] check_project 六维度扫描 + 评分（0-100）
- [x] generate_agents_md 自动生成 AGENTS.md
- [x] 三级边界系统（✅始终 / ⚠️询问 / 🚫禁止）

### ~~1.6 测试骨架 + CI~~ ✅
- [x] tests/ 目录：4 个模块 93 个测试用例
- [x] pyproject.toml pytest 配置
- [x] .pre-commit-config.yaml（ruff + 文件校验）
- [x] GitHub Actions CI（lint + test + spec + docs-sync）

### ~~1.7 文档同步强制~~ ✅
- [x] docs_sync_check 工具 + 硬编码映射表
- [x] CI job + pre-commit 双重拦截
- [x] AGENTS.md 显示完整规则

### ~~1.8 文档体系完善~~ ✅
- [x] docs/architecture.md / docs/development.md / docs/skills.md
- [x] docs/changelog.md / docs/contributing.md / docs/features.md / docs/roadmap.md
- [x] README.md 重写 + .env.example 修复
- [x] 根目录清理（25→20 项）

---

## 第三阶段：智能增强（🔄 进行中）

### 2.3 目标驱动长任务

- [ ] `/goal` 命令：设定完成条件，Agent 自主持续执行直到达成
- [ ] Agent 循环中自动检查目标是否达成
- [ ] 失败自动重试、变更自动 commit

**涉及文件：** `src/commands.py`、`src/llm.py`

### 2.4 项目级记忆

- [ ] 跨会话记住项目的构建命令、代码风格偏好、常用工具链、踩过的坑
- [ ] 存储为项目本地 `.agent-memory/` 目录，随 Git 版本控制

**涉及文件：** `src/tools/memory.py`

---

## 第四阶段：平台化（远期）

### 3.1 MCP 协议支持
- [ ] Model Context Protocol（MCP）服务器接入
- [ ] 支持 stdio / HTTP / SSE 三种传输类型
- [ ] MCP 工具自动注册为 Agent 可用工具

### 3.2 多厂商 LLM
- [ ] Claude / GPT / Qwen 后端接入
- [ ] 混合调用（思考用 Pro，执行用 Flash）

### 3.3 IDE/编辑器集成
- [ ] VS Code 扩展：在编辑器内对话，高亮代码片段

### 3.4 架构自进化
- [ ] Agent 能识别自己的性能瓶颈并生成改进提案
- [ ] 工具调用热替换（不停机更新工具实现）

---

## 版本里程碑

| 版本 | 目标 | 状态 |
| v0.8 | DeepSeek 前缀缓存优化（三段式上下文） | 🚧 进行中 |
| v0.9 | 目标驱动长任务 + 项目级记忆 | 🗓️ 规划中 |
| v1.0 | MCP + 多厂商 + IDE 集成 | 🗓️ 远期 |
| v0.4 | 多模型路由（Flash/Pro） | ✅ 已完成 |
| v0.5 | 项目规范检查 + AGENTS.md + 测试骨架 | ✅ 已完成 |
| v0.6 | Auto Mode 安全系统 | ✅ 已完成 |
| v0.7 | 文档同步强制 + 文档体系 + CI | ✅ 已完成 |
| v0.8 | 目标驱动长任务 + 项目级记忆 | 🗓️ 规划中 |
| v1.0 | MCP + 多厂商 + IDE 集成 | 🗓️ 远期 |

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
