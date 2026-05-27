# 功能列表

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
- [x] Token 预算管理：废弃硬编码字符上限，动态计算剩余 token 空间按比例分配工具结果「未找到」
