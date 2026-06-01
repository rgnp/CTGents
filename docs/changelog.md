# 变更日志

## v0.8 — 前缀缓存命中率优化（2026-06-01）

- 🚀 `CacheContext.send()` 严格过滤 `_volatile` 标记的前缀消息（对齐 Reasonix Volatile Scratch）
- 🔧 `_make_env_message()` 移除 `os.getcwd()` 动态内容 → 字节级稳定前缀，跨会话缓存命中
- 🔧 `_make_project_context()` 移除 `_volatile` 标记 → 项目上下文属于不可变前缀
- 🧪 测试更新：volatile 行为从"剥离字段"改为"完全过滤不发送"


## v0.7 — 目标驱动长任务（2026-05-31）

- 🎯 `/goal` 命令：设定目标后 Agent 自主执行直到完成
- 📦 GoalState 紧凑 JSON 状态（省 token）：计划/已完成/错误/历史摘要
- 🔄 3 层错误恢复：重试 → 换方案 → 暂停
- 🔥 代码修改后自动 importlib.reload（写完 src/ 文件无需重启）
- 📊 历史滑动窗口（最近 3 步完整，其余压缩为摘要）
- 🧪 28 个测试覆盖全部逻辑

## v0.6 — Auto Mode 安全系统（2026-05-29）

- 🛡️ 三级安全等级：SAFE（只读）/ RISKY（可逆）/ DANGEROUS（破坏性）
- 🎮 手动/自动双模式（`/mode` 命令）
- 🤝 会话信任机制（`/trust` 命令）
- ✅ 29 个测试覆盖全部安全逻辑

## v0.5 — 项目规范检查（2026-05-29）

- 📋 `check_project` 六维度扫描 + 评分
- 📝 `generate_agents_md` 自动生成 AGENTS.md
- 📄 AGENTS.md 面向 AI 的规范文件

## v0.4 — 多模型路由（2026-05-29）

- 🔄 Flash/Pro 双模型自动切换
- 🧠 任务复杂度感知路由
- ⌨️ `/model` 命令切换模型

## v0.3 — Git + 项目感知（2026-05-29）

- 🌿 Git 操作工具集（status/diff/commit/push/PR/log/branch）
- 🔍 scan_project 项目扫描
- 🌐 环境认知系统消息

## v0.2 — 核心工具补齐（2026-05-28）

- 📝 文件行级编辑（read_file_lines / edit_file_lines / undo_edit）
- 💻 Shell 执行（run_command / run_python）
- 🧩 插件系统 + 热加载
- 🧠 记忆系统（remember / recall / forget）
- 💾 会话管理（save / load / rename / export）

## v0.1 — 基础对话 Agent（2026-05-27）

- 🗣️ 多轮对话 + 流式输出
- 🔎 上网搜索（Tavily）
- 📄 文件读写
- 🔗 网页阅读（trafilatura）
- 🔄 API 自动重试
