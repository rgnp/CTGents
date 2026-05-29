# Agent

一个可扩展的 AI Agent 框架，支持多模型后端、插件系统、Auto Mode 安全系统。

[![CI](https://github.com/your-username/agent/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/agent/actions/workflows/ci.yml)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（复制后填入 API Key）
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和 TAVILY_API_KEY

# 3. 启动
python run.py
```

---

## 核心功能

| 类别 | 功能 | 命令 |
|------|------|------|
| 🗣️ **对话** | 流式输出 + 多轮对话 + Token 预算管理 | 直接输入 |
| 📝 **文件操作** | 读写 / 行级编辑 / 自动备份 / 撤销 | 对话驱动 |
| 💻 **命令执行** | Python 执行 / Shell 命令 / 安全黑名单 | 对话驱动 |
| 🌿 **Git 集成** | 状态 / diff / 自动提交 / 推送 / PR | 对话驱动 |
| 🔍 **项目感知** | 自动扫描技术栈 / 文件树 / 依赖 | `scan_project` |
| 📋 **规范检查** | 六维度评分 / 优化建议 / 自动修复 | `check_project` |
| 🛡️ **Auto Mode** | 三级安全等级 / 手动/自动模式 | `/mode` `/trust` |
| 🧩 **插件系统** | 自学安装 / 热加载 / Skill 技能包 | `install_plugin` |
| 🧠 **记忆系统** | 跨会话持久化 / 搜索 / 管理 | `remember` `recall` |
| 💾 **会话管理** | 保存 / 恢复 / 重命名 / 导出 | `/save` `/load` `/export` |
| 🔄 **热加载** | 改代码无需重启 | `/reload` |

---

## 快速命令参考

```bash
# 开发
make test        # 运行测试（93 个用例）
make lint        # 代码检查
make run         # 启动 Agent
make check       # 项目规范扫描

# 启动后
/mode auto       # 切换到自动模式（安全操作无需确认）
/mode manual     # 切换回手动模式
/trust write_file  # 信任 write_file，本会话自动放行
/model           # 查看/切换 LLM 模型
/help            # 查看所有指令
/exit            # 退出
```

---

## 安全模型

本系统内置 **三级工具安全等级**：

| 等级 | 说明 | manual 模式 | auto 模式 |
|------|------|:-----------:|:---------:|
| ✅ SAFE | 只读操作（读取/搜索/状态） | 自动放行 | 自动放行 |
| ⚠️ RISKY | 可逆写入（写文件/执行命令） | **需确认** | 自动放行 |
| 🚫 DANGEROUS | 破坏性（强制推送） | **需确认** | **需确认** |

---

## 技术栈

- **语言**: Python 3.11+
- **LLM 后端**: DeepSeek V3/V4 API（Flash + Pro 双模型）
- **搜索**: Tavily Search API
- **终端**: prompt_toolkit（增强交互）
- **网页解析**: trafilatura
- **代码检查**: ruff
- **测试**: pytest（93 个用例）
- **CI**: GitHub Actions

---

## 项目结构

```
agent/
├── src/                # 核心源码
│   ├── tools/          #   13 个工具模块
│   ├── safety.py       #   Auto Mode 安全系统
│   ├── llm.py          #   LLM 对话循环
│   ├── commands.py     #   指令系统
│   ├── main.py         #   主入口
│   ├── session.py      #   会话管理
│   └── config.py       #   配置管理
├── tests/              # 93 个测试用例
├── docs/               # 文档
├── memory/             # 跨会话记忆
├── plugins/            # 自学安装的插件
└── AGENTS.md           # AI 编程智能体操作手册
```

---

## 相关文档

| 文档 | 读者 | 内容 |
|------|------|------|
| [AGENTS.md](./AGENTS.md) | AI 编程智能体 | 构建命令、测试、代码风格、安全边界 |
| [docs/architecture.md](./docs/architecture.md) | 开发者 | 系统架构、数据流、设计决策 |
| [docs/development.md](./docs/development.md) | 开发者 | 添加工具/指令/插件的方法 |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | 贡献者 | 贡献流程、提交规范、测试要求 |
| [ROADMAP.md](./ROADMAP.md) | 所有人 | 开发路线图与版本计划 |
| [CHANGELOG.md](./CHANGELOG.md) | 所有人 | 版本变更记录 |
