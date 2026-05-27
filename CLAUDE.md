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
│   └── session.py    # 会话保存/恢复/摘要
├── .env / .env.example
├── requirements.txt
├── FEATURES.md       # 已有功能列表
├── ROADMAP.md        # 未来计划
└── sessions/         # 会话存档（不入 git）
```

## 代码规范

### 模块职责
- `main.py` — 入口，I/O 循环和 UI（print/input 仅在此文件）
- `config.py` — 只放配置、常量和 API 客户端初始化，不放业务逻辑
- `llm.py` — LLM 调用、流式输出、重试、对话路由（`run_conversation`）
- `tools.py` — 工具定义（TOOLS 列表）和工具执行逻辑；新增工具只改这个文件
- `session.py` — 会话持久化，不对接 UI

### 编写纪律
1. **所有函数必须有类型标注**（入参和返回值）
2. **用 `logging` 不用 `print`** — `print` 只允许在 `main.py` 中用于用户交互
3. **禁止全局可变状态** — 需要状态时在 `main()` 中持有，通过参数传入
4. **新增 API 依赖**必须在 `.env.example` 和 `config.py` 中同步
5. **模块间用相对导入**（`from .config import ...`）

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
