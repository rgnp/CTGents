# 变更日志

## 2026-07-28 — 核心项目与个人工作区拆分

- 新增 `src/paths.py`，用 `CTG_WORKSPACE_DIR` / `.ctg-workspace` / `~/.ctgents`
  统一定位个人 memory、knowledge、sessions、tasks 和 stats
- 内置 psyche/skills 移入 `src/ctgents_resources/` 并纳入 wheel package data；
  个人化 general psyche 原版转存个人知识库，核心版压缩为通用公开规则
- 所有测试运行状态统一重定向到 pytest 临时 workspace，修复测试污染真实工作回执
- 删除与 `test_main.py` 重复的 `test_finalize.py`，修正会话索引和控制信号的漂移测试
- 个人工作区作为独立本地 Git 项目，无公开 remote；会话、统计、回执、大 PDF 和外部项目默认忽略

## 2026-07-27 — Dashboard 与专属反思产物退役

- 按用户决定删除 `dashboard/` 的只读 Web 服务、collectors、页面和启动入口
- 删除 Dashboard 唯一消费的 `stats/*_reflection.json` 生产链、收尾钩子和 `/organs`
  反思体征，不再持续生成无人消费的重复快照
- 保留 tracker 原始工具事件、跨会话基线、异常检测、Gap、缓存统计、状态栏、Organs
  其余体征及会话保存/摘要，观测能力进入现有工作闭环而非独立页面

## 2026-07-27 — 第一轮证据化机制退役

- 退役 `check_project` 聚合总分和优秀/良好评级：它曾在报告红色问题时仍输出 100/100，
  不再允许启发式加分掩盖具体问题
- 删除 CI `spec-check >=80` 假质量门；保留 Ruff、全量 pytest 和 docs-sync 三个确定性门
- 删除 `TOOLS_LINT=[]` 后仍残留的 lint dispatcher，以及只有测试消费者的
  `generate_agents_md()` 公共兼容壳；内部生成器仍供 `check_project(fix=True)` 使用
- 删除与现役代码严重冲突的 `docs/features.md` 旧快照；历史变化统一查 changelog
- 新增 `docs/mechanism-retirement.md`，明确 protected / observe / retire / retired 证据合同

## 2026-07-20 — delegate "一委派就死寂" 三根修复

- 🔇 TUI 接通 worker 进度（set_progress_sink → 事件队列）：委派期间 transcript 显示
  单行原地更新的 worker 活动（原 print 被 Textual 吞掉 = 几十分钟死寂被误认卡死）
- ⛔ 批次闸（llm.\_handle_tool_results）：同一条消息连发多个 delegate 只放行第一个，
  其余预填拒绝并教正道（tool 配对契约不破坏）——实测 3 连发 = 主线串行停摆几十分钟
- 🧯 出处闸修 meta-mention 假阳性：反引号内 \`[已核]\` 是提及不是声称（worker 修完
  自述"每处 \`[已核]\` 已同行给 URL"曾被闸打回 → 自指重试循环）；反向测试钉死
  "反引号不豁免真声称"
- ↯ 闸未过的返回文案加重派止损："先针对问题清单改 brief，原样重派撞同一堵墙"
  （实测同一行连撞 4 次 × 整个 worker 预算）；工具描述注明"一次只派一个"

## 2026-07-17 — 心跳接通 paper-pipeline（无人跑论文入库）

- 🧬 心跳 worker 的 psyche 改事件化注入（`inject_psyche`，非拼 core 原文进前缀），
  过 `activate_skill` 的 owner 检查——worker 可自主 `load_psyche(paper-collection)` →
  `activate_skill(paper-pipeline, stage=resume)` 断点续跑论文入库
- 🔧 新工具 `fetch_paper`（arXiv ID/https 直链 → knowledge/ 下 PDF，魔数+大小校验，
  坏内容不落盘）与 `transcribe_paper`（PDF → paper.md 逐页转写）：pipeline 阶段 1/2
  的"去利刃"版，替代 skill 里的 run_python 即兴 requests/fitz 代码，两种模式共用
- 📜 paper-pipeline workflow 三处 python 块改用工具；阶段 4A（关联重建）需 run_python，
  无人期跳过记状态留给有人会话；网络预检改为"工具首败即视为断网"
- 🔐 心跳白名单加 skill 运行时 + 论文窄工具 + learn；依旧无 run_command/run_python/git；
  psyche 加载失败 fail-closed 本跳中止；`/heartbeat run` 后 resync 注册表防 worker
  psyche 泄漏进主会话自知状态

## 2026-07-17 — 心跳：无人期自主推进探索前沿

- 🫀 新模块 `src/heartbeat.py`：定时醒来消费 `tasks/frontier.md` 里用户种下的方向
  （搜集论文/探索领域），一跳领一项、预算 25 请求、断点写回 frontier 续跑
- 沉默为默认：无活跃项零 LLM 退出；工作落 knowledge/ 文件；用户回主会话时才收一条
  合并摘要（`main.run_agent_turn` append-only 注入，缓存安全）
- 防垃圾三件套：worker 前缀硬加载 research psyche（代码保证非散文提醒）+ knowledge/
  产出过 delegate_gate 机械出处闸（fail-visible）+ 候选方向只能等用户转正
- 防失控：连续 2 跳无推进自暂停（frontier 改动自动恢复）、每日 16 次上限、防重叠锁、
  无人期工具白名单（无 run_command/git/删除/remember）
- 入口：`/heartbeat [run]`、`python -m src.heartbeat [--loop N|--force|--status]`、
  `scripts/install_heartbeat_task.ps1`（schtasks 每 30 分钟）；文档 `docs/heartbeat.md`

## 2026-07-13 — 安全边界与 Eager 顺序修复

- 修正不可变安全核指向真实的 `src/tools/tool_guard.py`，并补机械拒写回归测试
- 专用 `git_push` 禁止 force-push `main/master`，覆盖普通分支名与 refspec
- Eager 只预执行连续 SAFE 前缀，防止读取跨过前序写操作；修复预置结果导致的重复执行

> 本文件在 v0.9 之后有一段较长的未记录空档（task_loop 动态重规划、psyche 系统、
> eager 并行工具执行、记忆 fingerprint 合并等均未补记）。下面新增条目从 2026-07-08 起接续，
> 中间空档暂不追溯，以代码和 `docs/architecture.md` 为准。

## 2026-07-08 — 研究知识库语义检索

- 🧠 `knowledge/`（400+ 文件，60+ 篇论文笔记）规模已到词面检索会漏召回的量级，
  `index_research_content` / `query_research` 新增本地 embedding 语义层（新模块
  `src/tools/embeddings.py`），词面零重合但语义相关的笔记也能被召回
- 🔒 纯本地离线，不要 API key（`sentence-transformers`，默认
  `paraphrase-multilingual-MiniLM-L12-v2`，首次用下载模型权重）
- 🛡️ 仅加分不兜底：未装包 / 模型加载失败 / `CTG_RAG_EMBED_ENABLED=0` 时静默退回纯 TF-IDF，
  行为和加这层之前完全一致
- ⚙️ `params.py` 新增旋钮：`embed_enabled` / `embed_model` / `embed_max_chars` / `lexical_weight`
- 🧪 新增 `tests/test_embeddings.py` + `test_rag.py` 混合检索用例（全部 mock 掉真实模型，
  不依赖网络/模型下载）
- 📌 范围仅 `knowledge/`（研究文档索引）；`memory/` 的 `recall`（跨会话行为记忆）未动，
  见 `docs/roadmap.md`「已知最薄弱」

## v0.9 — Web 工具缓存优化 + URL 重写（2026-06-01）

- ⚡ `search_web` + `read_page` 增加 TTL 缓存（搜索 5min，页面 10min），最大 200 条目
- ⏱️ `read_page` 改用 `urllib.request` 代替 `trafilatura.fetch_url`，增加 15s 超时控制
- ✂️ 页面内容超过 8000 字符自动截断，节省 token
- 🗑️ 缓存超过最大容量时自动淘汰最旧 20%
- 🛡️ 错误缓存分层：404/410 长缓存 10min，网络超时等临时错误短缓存 30s
- 📊 状态码透传：404→"页面不存在"，403→"拒绝访问"，429→"被限流"等精确消息
- 🔗 **URL 智能重写**：GitHub blob → raw.githubusercontent.com，arxiv HTML → abs 页面，解决 JS 渲染网站读取问题
- 🚀 `list_files` TTL 从 3s → 300s，对齐 web 工具缓存策略，避免同会话重复 IO
- 🧪 17 个测试覆盖缓存/截断/URL 重写全部场景
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
