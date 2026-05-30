# CTGents — CLI AI Agent

[![CI](https://github.com/rgnp/CTGents/actions/workflows/ci.yml/badge.svg)](https://github.com/rgnp/CTGents/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/rgnp/CTGents)](LICENSE)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek_V3%2FV4-green)](https://deepseek.com)

终端里的 AI 编程智能体。能读写文件、执行命令、操作 Git、搜索网页、分析项目结构，还能自学安装插件扩充能力。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和 TAVILY_API_KEY

# 3. 启动
python run.py
```

---

## 核心功能

| 类别 | 功能 |
|------|------|
| 🗣️ **对话** | 流式输出 / 多轮对话 / Token 预算管理 / 双模型路由 |
| 📝 **文件操作** | 读写 / 行级编辑 / 自动备份 / 一键撤销 |
| 💻 **命令执行** | Python 执行 / Shell 命令 / 安全黑名单 |
| 🌿 **Git 集成** | 状态 / diff / 自动提交 / 推送 / PR 创建 |
| 🔍 **项目感知** | 自动扫描技术栈 / 文件树 / 依赖 / 框架检测 |
| 📋 **规范检查** | 六维度评分 / 优化建议 / 自动修复（基于 2500+ GitHub 案例） |
| 🛡️ **Auto Mode** | 三级安全等级 / Manual + Auto 双模式 / 会话信任机制 |
| 🧩 **插件系统** | AI 自学安装 / 热加载 / Skill 技能包 |
| 🧠 **记忆系统** | 跨会话持久化 / 按需搜索 / 用户偏好学习 |
| 💾 **会话管理** | 保存 / 恢复 / 重命名 / 导出 |
| 🔄 **热加载** | 修改代码后 `/reload` 即时生效，无需重启 |

---

## 快速命令参考

```bash
# 开发
make test         # 运行测试（93 个用例）
make lint         # 代码检查（ruff）
make run          # 启动 Agent
make check        # 项目规范扫描
make preflight    # 一站式：lint + test + docs-sync + check

# 启动后
/mode auto        # 自动模式（安全操作无需确认）
/mode manual      # 手动模式（每次确认）
/trust write_file # 信任某工具，本会话自动放行
/model            # 查看/切换 LLM 模型
/help             # 查看所有指令
/exit             # 退出
```

---

## 安全模型

内置 **三级工具安全等级**，在自动和手动之间灵活切换：

| 等级 | 说明 | manual 模式 | auto 模式 |
|------|------|:-----------:|:---------:|
| ✅ SAFE | 只读操作（读取/搜索/状态） | 自动放行 | 自动放行 |
| ⚠️ RISKY | 可逆写入（写文件/执行命令） | **需确认** | 自动放行 |
| 🚫 DANGEROUS | 破坏性（强制推送） | **需确认** | **需确认** |

---

## 技术栈

- **语言**: Python 3.11+
- **LLM 后端**: DeepSeek V3/V4 API（Flash 省钱 + Pro 强推理，自动路由）
- **搜索**: Tavily Search API
- **终端**: prompt_toolkit（增强交互）
- **网页解析**: trafilatura
- **代码检查**: ruff
- **测试**: pytest（93 个用例）
- **CI**: GitHub Actions

---

## 项目结构

```
├── src/                 # 核心源码
│   ├── tools/           #   13 个工具模块
│   │   ├── file.py      #   文件读写/编辑/备份
│   │   ├── exec.py      #   Python/Shell 执行
│   │   ├── git.py       #   Git 全流程操作
│   │   ├── project.py   #   项目扫描/分析
│   │   ├── lint.py      #   规范检查/文档同步
│   │   ├── web.py       #   网页搜索/阅读
│   │   ├── code.py      #   代码搜索
│   │   ├── memory.py    #   记忆系统
│   │   ├── think.py     #   策略思考
│   │   ├── discover.py  #   能力扫描
│   │   ├── plugin_mgr.py#   插件管理
│   │   ├── tokens.py    #   Token 预算
│   │   └── __init__.py  #   工具注册中心
│   ├── safety.py        #   Auto Mode 安全系统
│   ├── commands.py      #   指令系统（/help /mode /save 等）
│   ├── config.py        #   配置管理
│   ├── llm.py           #   LLM 对话循环 + 双模型路由
│   ├── main.py          #   主入口 + UI 循环
│   └── session.py       #   会话持久化
├── tests/               # 93 个测试用例
├── docs/                # 文档
├── memory/              # 跨会话记忆（Markdown）
├── sessions/            # 对话历史（自动管理，不提交）
├── plugins/             # 自学安装的插件（自动管理，不提交）
├── AGENTS.md            # AI 编程智能体操作手册
├── Makefile             # 构建/测试/运行
├── pyproject.toml       # 包配置 + ruff + pytest
└── run.py               # 启动入口
```

---

## 相关文档

| 文档 | 读者 | 内容 |
|------|------|------|
| [AGENTS.md](./AGENTS.md) | AI 编程智能体 | 构建命令、测试、代码风格、安全边界 |
| [docs/architecture.md](./docs/architecture.md) | 开发者 | 系统架构、数据流、设计决策 |
| [docs/development.md](./docs/development.md) | 开发者 | 添加工具/指令/插件的方法 |
| [docs/contributing.md](./docs/contributing.md) | 贡献者 | 贡献流程、提交规范、测试要求 |
| [docs/roadmap.md](./docs/roadmap.md) | 所有人 | 开发路线图与版本计划 |
| [docs/features.md](./docs/features.md) | 所有人 | 完整功能列表 |
| [docs/skills.md](./docs/skills.md) | 开发者 | Skill 技能包开发参考 |
| [docs/cache-design.md](./docs/cache-design.md) | 开发者 | DeepSeek 前缀缓存优化 |

---

## 许可证

[MIT](LICENSE)
