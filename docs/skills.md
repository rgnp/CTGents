# Skill 技能包参考

本项目支持 [Agent Skills 开放标准](https://github.com/agent-skill-labs/agent-skills-spec) 的 Skill 技能包。

## 什么是 Skill？

Skill 是可复用的知识包，包含 YAML frontmatter 元数据 + Markdown 指令。
Agent 可以按需加载 Skill，获得特定领域的专业知识。

## 命令参考

```bash
skill_learn(topic="overview")           # Skill 规范概览
skill_learn(topic="spec")               # YAML 格式 / 命名规则
skill_learn(topic="best-practices")     # 最佳实践

skill_template(category="minimal")      # 最简模板
skill_template(category="standard")     # 标准模板
skill_template(category="code")         # 含脚本模板

skill_create(name="my-skill", ...)       # 创建 Skill
skill_list()                             # 列出所有 Skill
skill_show(skill_path="my-skill")        # 查看 Skill 内容
skill_validate(filepath="...")           # 验证格式

skill_load(name="my-skill")             # 加载 Skill
skill_unload(name="my-skill")           # 卸载 Skill
```

## Skill 目录结构

```
skills/my-skill/
├── SKILL.md           # 主文件（YAML + 指令）
├── scripts/           # 可执行脚本
├── references/        # 参考文档
└── assets/            # 静态资源
```

## 使用场景

- 代码审查规范
- 周报/报告生成模板
- 数据库查询助手
- 部署流程指南
