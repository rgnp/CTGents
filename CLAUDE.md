# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概况

Python agent 项目，使用 DeepSeek API（兼容 OpenAI SDK）+ Tavily 搜索。

## 运行

```bash
cd agent
cp .env.example .env            # 填入 API Keys
pip install -r requirements.txt
python -m src.main
```

## 项目结构

```
agent/
├── src/
│   ├── main.py       # 入口 + I/O 循环
│   ├── config.py     # 环境变量、常量、API 客户端
│   ├── llm.py        # LLM 调用（流式/非流式）、对话路由
│   ├── tools.py      # 工具定义 + 执行
│   ├── session.py    # 会话保存/恢复/摘要
│   └── ...           # 同类模块增多时下沉子目录（如 memory/、skills/）
├── .env / .env.example
├── requirements.txt
├── FEATURES.md       # 已有功能列表
├── ROADMAP.md        # 未来计划
└── sessions/         # 会话存档（不入 git）
```

## 代码规范

### 模块职责
- `main.py` — 入口，I/O 循环和 UI。**唯一的 `print`/`input` 在此文件**；所有用户可见的输出通过回调或返回值流入此层
- `config.py` — 只放配置、常量和 API 客户端初始化，不放业务逻辑
- `llm.py` — LLM 调用、流式输出、重试、对话路由。**不碰终端**，输出通过 `TokenCallback` 回调传出
- `tools.py` — 工具定义（TOOLS 列表）和工具执行逻辑；新增工具只改这个文件
- `session.py` — 会话持久化，不对接 UI

### 结构化设计
- **高内聚低耦合**：相关的放一起（改一个功能只动一个模块），不相关的互不依赖（模块间接口越窄越好）
- 每个文件单一职责，按「是什么」而非「做什么」拆分
- 新功能先想：是扩展现有模块，还是一个全新领域？后者才建新文件或子目录
- 同类模块超过 2 个时，下沉到子目录（如 `src/tools/`、`src/memory/`）
- 模块间通过函数参数传递数据，不通过全局变量
- 一个文件超过 150 行 → 审视是否该拆

### 函数内规范
- **纯逻辑与副作用分离**：API 调用、文件读写是副作用，独立成函数。输出通过回调（`Callable[[str], None]`）传给 UI 层，不直接 `print`
- **模块间接口最小化**：只暴露必要的函数，内部实现细节用 `_` 前缀标记为私有
- **数据流单向清晰**：函数入参 → 处理 → return 结果，不要函数内部去读全局变量或改外部状态
- **不写万能函数**：一个函数只做一件事。超过 30 行 → 审视是否该拆
- **异常在最外层处理**：底层函数直接抛，不要每层 try/except；只有 `main.py` 和 `llm.py` 的重试逻辑可以 catch
- **常量不进函数体**：阈值、路径、配置一律放在 `config.py`，函数内不出现裸数字/裸字符串
- **常量必须有注释说明依据**：每个常量写一行注释说明「为什么是这个值」，不能只写名称。可调整的参数标记「可随时按实际效果调整」

### 编写纪律
1. **所有函数必须有类型标注**（入参和返回值）
2. **UI 与逻辑解耦**：业务层（llm.py、tools.py、session.py）不直接 `print`，通过回调（如 `TokenCallback`）或返回值把数据交给 `main.py` 展示
3. **用 `logging` 记录系统消息** — 状态通知、警告、错误用 `logger`，只有用户聊天内容用回调传
4. **禁止全局可变状态** — 需要状态时在 `main()` 中持有，通过参数传入
5. **新增 API 依赖**必须在 `.env.example` 和 `config.py` 中同步
6. **模块间用相对导入**（`from .config import ...`）

### 新增功能流程
1. 在 `tools.py` 的 `TOOLS` 列表中添加工具定义
2. 在 `tools.py` 的 `execute_tool()` 中添加执行分支
3. 如需要新依赖，更新 `requirements.txt` 和 `config.py`
4. 功能跑通后更新 `FEATURES.md`，然后 git commit

## 开发原则

- 增量开发，每次只加一小块
- 每个增量必须是完整闭环（端到端能跑）
- 鲁棒性是底线 — 宁可少做，不可做脆
- 功能优先，人格/情绪是最后一张皮
