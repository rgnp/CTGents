# 系统架构

## 整体架构

```
┌──────────────────────────────────────────────┐
│                  main.py                      │
│        主入口 · UI 循环 · 热加载              │
├──────────────────────────────────────────────┤
│                  llm.py                       │
│     LLM 对话循环 · 模型路由 · 安全检查        │
├──────────────────────────────────────────────┤
│  commands.py      │    safety.py             │
│  指令系统          │   Auto Mode 安全系统     │
│  /help /mode /save│   三级等级 · 双模式      │
│  /load /trust...  │   会话信任               │
├──────────────────────────────────────────────┤
│              tools/ (14 个模块)              │
│  file  exec  git  project  lint  web  code   │
│  memory  think  discover  plugin_mgr  tokens  │
│  mcp  rag                                      │
├──────────────────────────────────────────────┤
│  config.py  │  session.py  │  plugins/       │
│  配置管理    │  会话持久化   │  插件热加载     │
└──────────────────────────────────────────────┘
```

## 核心模块说明

### main.py — 主入口

- 注入环境/项目/记忆/安全模式四层上下文到 system prompt
- 处理 `/reload` 热加载、对话流式输出
- 集成 Esc 打断监听（Windows msvcrt）

### llm.py — LLM 对话循环
- **LLMBackend** 抽象基类 + **DeepSeekBackend** 实现
- 双模型路由：Flash（省钱） / Pro（强推理），自动按任务复杂度切换
- 工具调用循环 + **Auto Mode 安全检查**（manual/auto 模式 + 用户确认）
- 流式优先，失败退化为非流式，带指数退避重试
- 上下文 Token 预算管理，超限自动拦截

### safety.py — Auto Mode 安全系统
- 三级安全等级：**SAFE**（只读自动放行）→ **RISKY**（可逆写）→ **DANGEROUS**（破坏性）
- 双模式：**manual**（需确认） / **auto**（RISKY 自动放行）
- 会话信任机制：`/trust <工具名>` 本会话自动放行
- 29 个内置工具已标注安全等级

### commands.py — 指令系统
- 结构化注册：`@builtin` / `@builtin_multi` 装饰器
- 11 条内置指令自动聚合到 `/help`
- 插件 COMMANDS 热注册

### config.py — 配置管理
- 环境变量驱动（`.env`），无硬编码密钥
- 双模型独立配置：MODEL_FLASH / MODEL_PRO


### rag.py — RAG 代码索引与语义搜索
- 扫描项目文件，智能分块（按函数/类/行数），建立 TF-IDF 加权索引
- BM25 评分 + 代码语义关键词加权（函数名×3、注释×2、标识符×1.5）
- 零额外依赖，纯 Python 实现
- 增量更新：文件哈希缓存，只重新索引变更的文件
- 提供三个工具：`rag_index`（建立/更新索引）、`rag_query`（语义搜索）、`rag_status`（查看状态）
- AI 在对话中主动调用 `rag_query()` 进行代码检索（Agentic RAG 模式），不破坏 DeepSeek 前缀缓存
- 零额外依赖，增量更新只重新索引变更的文件

### session.py — 会话管理
- JSON 持久化，自动摘要生成
- 过滤 `_volatile` 运行时注入消息

## 数据流：一次对话

```
用户输入 → main.py (UI 循环)
  → llm.py (run_conversation)
    → auto_select_model (Flash/Pro)
    → _invoke_llm (流式调用)
    → LLM 返回 tool_calls
      → 遍历 tool_calls
        → safety.check_tool (安全检查)
          → 如果是 confirm: 交互式确认
        → execute_tool (执行工具)
        → truncate_to_budget (截断)
      → 结果追加到 messages
    → 循环直到 LLM 返回纯文本
  → 输出到终端
```

## 工具模块规范

每个 tools/ 模块遵循统一模式：
1. 定义 `TOOLS_XXX` 列表（OpenAI function calling 格式）
2. 实现 `execute(name, args)` 调度函数
3. 在 `__init__.py` 的 `_BUILTIN_MODULES` 中注册
4. 在 `safety.py` 标注安全等级

## 关键设计决策

- **模型无关**：核心逻辑不依赖特定模型，LLM 可插拔
- **安全优先**：所有自动化操作有安全护栏，用户始终有最终控制权
- **热加载**：`/reload` 刷新指令 + 工具 + 插件，无需重启
- **变更追踪**：文件编辑后自动 grep 全项目找到需同步的文档
