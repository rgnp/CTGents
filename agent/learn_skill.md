# learn_skill.md

## You

你是个skill插件怎么用？

## Agent

*[调用工具: list_plugins]*

好问题！让我给你演示一下这个 **agent_skills 插件** 到底怎么用，有哪些功能。

先看看已经安装好的工具列表：

*[工具结果: 已安装插件：   agent_skills  (6 个工具, 28476B)]*

## Agent

*[调用工具: skill_list]*

*[工具结果: 📋 共找到 1 个 Skill：    1. markdown-report      📝 当用户需要生成 Markdown 格式的专业报告时使用此技能。适用于周报、技术文档、项目总结等场景。      📂 D:\project\agent...]*

好的，现在来手把手演示这个插件的 **每一个功能**👇

---

## 🛠️ agent_skills 插件 **完整使用教程**

这个插件目前有 **6 个工具**，覆盖了 Skill 的"学→看→建→查→验→管"全流程：

---

### 1️⃣ 📚 学习篇：`skill_learn`

想了解什么是 Skill，直接查！

```
skill_learn(topic="overview")       → 全面概览
skill_learn(topic="spec")           → 规范详解（YAML格式、命名规则）
skill_learn(topic="best-practices") → 最佳实践（怎么写好一个 Skill）
```

✅ **适用场景：** 新手入门、写 Skill 前复习规范

---

### 2️⃣ 📄 模板篇：`skill_template`

不想从零开始？直接拿模板改

```
skill_template(category="minimal")   → 最简模板（纯指令）
skill_template(category="standard")  → 标准模板（带示例）
skill_template(category="code")      → 含脚本模板（带 Python 脚本引用）
```

✅ **适用场景：** 快速开始创建自己的 Skill

---

### 3️⃣ 🆕 创建篇：`skill_create`

这是核心功能——创建一个符合开放标准的 Skill 目录

```
skill_create(
    name="my-skill",           # 小写+数字+连字符，最长64字符
    description="...",         # 描述做什么、何时用，最长1024字符
    instructions="...",        # Markdown 指令内容
    license="MIT",             # 可选：许可证
    compatibility="...",       # 可选：兼容性说明
    create_scripts_dir=True,   # 可选：创建 scripts/ 目录
    create_references_dir=True, # 可选：创建 references/ 目录
    create_assets_dir=True     # 可选：创建 assets/ 目录
)
```

生成的结构：
```
skills/my-skill/
├── SKILL.md           ← 自动生成，含 YAML + 指令
├── scripts/           ← 放可执行脚本
│   └── README.md
├── references/        ← 放参考文档
└── assets/            ← 放静态资源
```

✅ **适用场景：** 打包专业知识、工作流程、最佳实践

---

### 4️⃣ 📋 列表篇：`skill_list`

查看所有已安装的 Skills

```
skill_list()                      → 列出当前目录下 skills/ 中的所有 Skill
skill_list(skills_dir="其他路径")  → 指定目录搜索
```

✅ **适用场景：** 看看自己有哪些 Skill，快速导航

---

### 5️⃣ 📖 查看篇：`skill_show`

查看某个 Skill 的完整内容（元数据 + 指令 + 目录结构）

```
skill_show(skill_path="my-skill")          # 按名称查（自动在 skills/ 下找）
skill_show(skill_path="skills/my-skill")   # 按路径查
```

✅ **适用场景：** 回顾 Skill 内容、检查配置、分享给他人

---

### 6️⃣ ✅ 验证篇：`skill_validate`

检查你的 SKILL.md 是否符合规范

```
skill_validate(filepath="skills/my-skill/SKILL.md")
```

会检查：
- ✅ YAML frontmatter 格式
- ✅ name 命名规范
- ✅ description 长度
- ✅ 指令内容非空
- ✅ 预估 Token 消耗
- ⚠️ 发出告警建议

✅ **适用场景：** 创建/编辑 Skill 后做质量检查

---

## 🎯 典型使用流程

```
新手入门                    已有 Skill 想修改
┌──────────┐               ┌─────────────┐
│ skill_learn │               │ skill_show   │
└─────┬────┘               └──────┬──────┘
      │                            │
      ▼                            ▼
┌──────────┐               ┌──────────────┐
│skill_template│              │skill_validate │
└─────┬────┘               └──────┬───────┘
      │                            │
      ▼                            ▼
┌──────────┐               ┌──────────────┐
│skill_create│              │  修改后再次    │
└─────┬────┘               │skill_validate  │
      │                    └──────────────┘
      ▼
┌──────────┐
│skill_list │  ← 确认已创建
└──────────┘
```

---

要不我现在就拿一个真实场景给你演示一遍？比如：

- 🔍 **学一下** Skill 规范
- 📄 **拿个模板**
- 🆕 **创建一个"代码审查"Skill**
- 📋 **列出所有 Skill**
- 📖 **查看刚创建的 Skill**
- ✅ **验证格式是否规范**

或者你想让我 **帮你创建一个特定的 Skill**（比如数据分析、周报生成、代码规范审查之类的）？
