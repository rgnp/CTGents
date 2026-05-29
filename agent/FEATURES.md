# 功能列表

## 2026-05-29
- [x] 环境认知：系统消息注入工作目录、当前日期时间、操作系统，保存时自动过滤
- [x] /skill 命令：list/show/load/context/reload/create/validate 子命令
- [x] /help 别名合并：同 handler 的多个别名合并显示为一行
- [x] /reload 热加载：无需重启，刷新 commands + 插件 TOOLS + 插件 COMMANDS
- [x] /plugin 改进：显示 DESCRIPTION + 工具列表
- [x] 指令系统结构化：Command(name, description, usage, handler) + builtin/builtin_multi 装饰器
- [x] 插件 COMMANDS 支持两种格式：dict{name: handler} 或 list[Command]
- [x] Skill 插件：agent 自学 Agent Skills 开放标准，自安装 agent_skills 插件（11 个工具）
- [x] 插件接口规范（PLUGIN_SPEC）：TOOLS + execute + DESCRIPTION，agent 可调用查询
- [x] /pop 撤回对话、/edit 修改重发、/export 导出 Markdown、/run 直接调工具
- [x] Esc 撤回：prompt_toolkit 绑定 Esc 键，撤回最后一条用户消息
- [x] Ctrl+C 中断：中断 LLM 调用后可输入指导继续对话

## 2026-05-28
- [x] 插件系统：discover_plugins 扫描、install_plugin 安装并热加载、list_plugins 列出
- [x] Agent 自进化：上网学习 → 写 Python 插件代码 → 安装 → 获得新能力
- [x] Agent 自装插件：word_count（文本统计）、json_tools（JSON 格式化/校验）
- [x] 统一工具调度：execute_tool 遍历插件 + 内置模块，不再 if-else 链
- [x] /rename 重命名会话、/sessions 历史列表、/load 切换、/new 新建
- [x] think 工具：策略规划，在接到复杂任务/搜索后评估/发现新线索时调用
- [x] discover 工具：一次调用扫描所有能力（内置工具 + 已安装插件 + 可用 Skill）

## 2026-05-27
- [x] 基础对话回路：接收用户输入 → 调用 DeepSeek API → 返回回复
- [x] 多轮对话：通过内存中的 messages 列表自动保持上下文
- [x] 对话退出：/exit 命令和 Ctrl+C 两种方式退出
- [x] 上网搜索：通过 Tavily API 自动搜索，DeepSeek 函数调用驱动，最多 5 轮工具调用
- [x] 模块化重构：config.py / tools.py / agent.py 三文件分离
- [x] API 自动重试：网络波动/限流自动指数退避重试（最多 3 次），认证错误立即失败
- [x] 消息回滚：重试全部失败后对话状态不变，用户可重新输入
- [x] 搜索结果格式化：仅保留 title/url/content，瘦身 token；空结果如实反馈
- [x] 会话保存与恢复：/exit 时自动保存 messages + LLM 生成摘要；启动时可选加载历史会话并回显最近对话
- [x] 流式输出：LLM 回复逐字打印；首试流式，网络波动退化为非流式重试
- [x] 网页阅读：搜索到链接后可打开网页提取全文（trafilatura），截断至 8000 字符
- [x] 文件读写：agent 可读取项目文件、创建/覆写本地文件（read_file / write_file）
- [x] 目录浏览：agent 可列出目录结构和文件（list_files）
- [x] 代码执行：agent 可执行 Python 代码并获取输出，子进程隔离 + 超时保护（run_python）
- [x] 代码搜索：正则搜索项目代码，找到所有引用（grep_code）
- [x] 文件删除：补齐文件 CRUD 四件套（delete_file）
- [x] Token 预算管理：废弃硬编码字符上限，动态计算剩余 token 空间按比例分配工具结果
