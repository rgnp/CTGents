# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概况

Python agent 项目，使用 DeepSeek API（兼容 OpenAI SDK）+ Tavily 搜索。

## 运行

```bash
cd agent
cp .env.example .env          # 填入 API Keys
pip install -r requirements.txt
python agent.py
```

## 项目结构

```
agent/
├── config.py     # 所有环境变量、API 客户端、常量，统一管理
├── tools.py      # 工具定义（TOOLS 列表）和工具执行（execute_tool）
├── agent.py      # 主循环：I/O → LLM 调用 → 工具路由 → 返回
├── .env          # API Keys（不入 git）
├── .env.example  # 配置模板
├── requirements.txt
├── FEATURES.md   # 已有功能列表
├── ROADMAP.md    # 未来计划
```

## 代码规范

### 模块职责
- `config.py` — 只放配置、常量和 API 客户端初始化，不放业务逻辑
- `tools.py` — 工具定义（LLM 可调用的函数 schema）和工具执行逻辑；新增工具只改这个文件
- `agent.py` — 主入口，负责 I/O 循环和 LLM 对话路由；`main()` 是唯一入口

### 编写纪律
1. **所有函数必须有类型标注**（入参和返回值）
2. **用 `logging` 不用 `print`** — `print` 只允许在 `main()` 中用于用户交互
3. **禁止全局可变状态** — 需要状态时在 `main()` 中持有，通过参数传入
4. **新增 API 依赖**必须在 `.env.example` 和 `config.py` 中同步

### 新增功能流程
1. 在 `tools.py` 的 `TOOLS` 列表中添加工具定义
2. 在 `tools.py` 的 `execute_tool()` 中添加执行分支
3. 如需要新依赖，更新 `requirements.txt` 和 `config.py`
4. 功能跑通后更新 `FEATURES.md`

## 开发原则

- 增量开发，每次只加一小块
- 每个增量必须是完整闭环（端到端能跑）
- 鲁棒性是底线 — 宁可少做，不可做脆
- 功能优先，人格/情绪是最后一张皮
