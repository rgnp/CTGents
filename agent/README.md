# Agent

一个可扩展的 AI Agent 框架，支持多模型后端、插件系统、Skill 技能包。

## 快速开始

```bash
pip install -r requirements.txt
python run.py
```

- 🔍 **项目感知** — 自动扫描项目结构、语言、框架、依赖、构建命令
- 📋 **规范检查** — 六维度扫描项目规范（基于 2500+ 案例的六大军规），评分 + 优化建议
- 📝 **AGENTS.md** — 自动生成/更新面向 AI 的规范文件，定义命令/边界/工作流
- 📝 文件行级编辑（read_file_lines / edit_file_lines / undo_edit）
- 💻 Shell 命令执行（Python / 通用命令 / 代码搜索）
- 🌿 **Git 集成** — 状态查看、差异对比、自动提交、推送、PR
- 🔍 **项目感知** — 自动扫描项目结构、语言、框架、依赖、构建命令
- 🧩 插件系统（自学安装新能力 + 热加载）
- 📦 Skill 技能包（加载/卸载/自动匹配）
- 🧠 跨会话记忆系统（持久化/回忆/遗忘）
- 💾 会话管理（保存/恢复/重命名/导出）
- 🔄 热加载（改代码无需重启）

## 技术栈

- Python 3.11+
- DeepSeek V3/V4 API
- Tavily Search API
- prompt_toolkit（增强交互）
- trafilatura（网页文本提取）

## 项目结构

```
agent/
├── src/
│   ├── tools/          # 工具模块（文件/Git/Shell/项目感知/网络/记忆等）
│   ├── commands.py     # 指令系统（/help /save /load 等）
│   ├── config.py       # 配置管理
│   ├── llm.py          # LLM 对话循环
│   ├── main.py         # 主入口
│   └── session.py      # 会话管理
├── memory/             # 跨会话记忆
├── sessions/           # 对话历史
├── plugins/            # 自学安装的插件
└── run.py              # 启动入口
```
- 跨会话记忆系统
