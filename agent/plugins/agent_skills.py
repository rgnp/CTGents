"""
agent_skills — Agent Skills 完整管理插件 v2.0

集成 Skill 管理 + 自动发现 + 智能匹配 + 自动加载 于一体。
"""
import os, re
from pathlib import Path

DESCRIPTION = "提供 Agent Skills 的完整生命周期管理：发现扫描、列表查看、详情展示、创建新 Skill、格式验证、模板生成、学习资料、智能匹配、自动加载/卸载、活跃上下文查看。适用于管理与复用各类专业技能的 Skill 系统。"

# ============================================================
# 全局状态
# ============================================================
_SKILL_INDEX = {}
_ACTIVE_SKILLS = {}
SKILLS_BASE = "skills"


# ============================================================
# 核心函数
# ============================================================

def _parse_yaml_header(content):
    """解析 SKILL.md 的 YAML 元数据头。支持 > 折叠块。"""
    match = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', content, re.DOTALL)
    if not match:
        return {}
    yaml_text = match.group(1)
    meta = {}
    current_key = None
    current_value_lines = []
    is_folded = False

    for line in yaml_text.split('\n'):
        # 缩进行（以空格或 tab 开头）→ 续接上一个 key 的值
        if re.match(r'^[ \t]', line):
            stripped = line.strip()
            if stripped:
                current_value_lines.append(stripped)
            continue

        # 新 key: value 行
        if ':' in line:
            # 先保存上一个 key
            if current_key is not None:
                value = ' '.join(current_value_lines) if is_folded else '\n'.join(current_value_lines)
                meta[current_key] = value

            key, _, value_part = line.partition(':')
            current_key = key.strip()
            value_part = value_part.strip()
            if value_part == '>':
                is_folded = True
                current_value_lines = []
            else:
                is_folded = False
                current_value_lines = [value_part] if value_part else []
        else:
            # 没有冒号的行（极少见），忽略
            continue

    # 最后一个 key
    if current_key is not None:
        value = ' '.join(current_value_lines) if is_folded else '\n'.join(current_value_lines)
        meta[current_key] = value

    return meta


def _scan_skills():
    """扫描所有 Skill 目录。"""
    index = {}
    base = Path(SKILLS_BASE)
    if not base.is_dir():
        return index
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            meta = _parse_yaml_header(content)
            name = meta.get("name", entry.name)
            desc = meta.get("description", "")
            index[name] = {
                "name": name, "description": desc,
                "path": str(entry), "filepath": str(skill_file),
                "content": content,
            }
        except Exception:
            continue
    return index


def _compute_relevance(user_input, skill_name, skill_desc):
    """计算相关性得分。"""
    if not user_input:
        return 0
    text = (skill_name + " " + skill_desc).lower()
    inp = user_input.lower()
    input_words = set(re.findall(r'[\w\u4e00-\u9fff]+', inp))
    skill_words = set(re.findall(r'[\w\u4e00-\u9fff]+', text))
    if not input_words:
        return 0
    common = input_words & skill_words
    score = len(common) / max(len(input_words), 1)
    if skill_name.lower() in inp:
        score += 0.5
    return min(score, 2.0)


# ============================================================
# 工具函数
# ============================================================

def _list_skills(target_dir=None):
    """列出所有 Skill。"""
    global _SKILL_INDEX
    _SKILL_INDEX = _scan_skills()
    if not _SKILL_INDEX:
        return "📭 未找到任何 Skill（skills/ 目录下未发现 SKILL.md）"
    lines = [f"📋 共找到 {len(_SKILL_INDEX)} 个 Skill：\n"]
    for i, (name, info) in enumerate(sorted(_SKILL_INDEX.items()), 1):
        desc = info["description"]
        if len(desc) > 80:
            desc = desc[:80] + "..."
        lines.append(f"  {i}. {name}")
        lines.append(f"     📝 {desc}")
        lines.append(f"     📂 {info['path']}")
    return "\n".join(lines)


def _show_skill(skill_path, skills_base=None):
    """显示 Skill 详情。"""
    global _SKILL_INDEX
    if not _SKILL_INDEX:
        _SKILL_INDEX = _scan_skills()
    
    # 尝试直接匹配
    info = _SKILL_INDEX.get(skill_path)
    # 前缀匹配
    if not info:
        for name, _info in _SKILL_INDEX.items():
            if name.startswith(skill_path) or skill_path in name:
                info = _info
                break
    # 路径匹配
    if not info:
        p = Path(skill_path)
        if p.is_file() and p.name == "SKILL.md":
            try:
                content = p.read_text(encoding="utf-8")
                meta = _parse_yaml_header(content)
                info = {
                    "name": meta.get("name", p.parent.name),
                    "description": meta.get("description", ""),
                    "path": str(p.parent),
                    "filepath": str(p),
                    "content": content,
                }
            except Exception:
                pass
        elif p.is_dir() and (p / "SKILL.md").is_file():
            try:
                content = (p / "SKILL.md").read_text(encoding="utf-8")
                meta = _parse_yaml_header(content)
                info = {
                    "name": meta.get("name", p.name),
                    "description": meta.get("description", ""),
                    "path": str(p),
                    "filepath": str(p / "SKILL.md"),
                    "content": content,
                }
            except Exception:
                pass
    
    if not info:
        available = ", ".join(sorted(_SKILL_INDEX.keys())) if _SKILL_INDEX else "无"
        return f"❌ 未找到 Skill「{skill_path}」。可用：{available}"
    
    content = info["content"]
    # 分离 YAML 头和正文
    meta_text = ""
    body = content
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if m:
        meta_text = m.group(1)
        body = content[m.end():].strip()
    
    desc = info["description"]
    if len(desc) > 100:
        desc = desc[:100] + "..."
    
    lines = [
        f"━━━ Skill 详情 ━━━",
        f"📂 位置: {info['path']}",
        f"",
        f"📋 元数据:",
        f"  name: {info['name']}",
        f"  description: {desc}",
        f"",
        f"━━━ 指令内容（{len(body)} 字符）━━━",
        body[:3000],  # 限制显示
    ]
    if len(body) > 3000:
        lines.append(f"...（共 {len(body)} 字符，仅显示前 3000 字符）")
    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _create_skill(name, description, instructions, output_dir=None, license=None,
                  compatibility=None, create_scripts_dir=False,
                  create_references_dir=False, create_assets_dir=False):
    """创建新 Skill。"""
    base = Path(output_dir or SKILLS_BASE)
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    meta_lines = ["---"]
    meta_lines.append(f"name: {name}")
    meta_lines.append(f"description: >")
    for line in description.split('\n'):
        meta_lines.append(f"  {line}")
    if license:
        meta_lines.append(f"license: {license}")
    if compatibility:
        meta_lines.append(f"compatibility: {compatibility}")
    meta_lines.append("---")
    
    skill_content = "\n".join(meta_lines) + "\n\n" + instructions
    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
    
    if create_scripts_dir:
        (skill_dir / "scripts").mkdir(exist_ok=True)
    if create_references_dir:
        (skill_dir / "references").mkdir(exist_ok=True)
    if create_assets_dir:
        (skill_dir / "assets").mkdir(exist_ok=True)
    
    # 刷新索引
    global _SKILL_INDEX
    _SKILL_INDEX = _scan_skills()
    
    return (
        f"✅ Skill 创建成功！\n"
        f"\n"
        f"📂 路径: {skill_dir}\n"
        f"📄 SKILL.md: {skill_dir / 'SKILL.md'}\n"
        f"\n"
        f"━━━ 元数据 ━━━\n"
        f"名称: {name}\n"
        f"描述: {description[:80]}{'...' if len(description) > 80 else ''}\n"
        f"许可证: {license or '（未设置）'}\n"
        f"兼容性: {compatibility or '（未设置）'}\n"
        f"━━━━━━━━━━━━\n"
        f"\n"
        f"💡 提示：用 skill_show 查看完整内容，用 skill_validate 验证格式。"
    )


def _validate_skill(filepath):
    """验证 SKILL.md 格式。"""
    p = Path(filepath)
    if not p.is_file():
        return f"❌ 文件不存在：{filepath}"
    
    content = p.read_text(encoding="utf-8")
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    
    errors = []
    if not m:
        errors.append("缺少 YAML 元数据头（--- 分隔的区块）")
        meta = {}
    else:
        meta = _parse_yaml_header(content)
        if "name" not in meta:
            errors.append("缺少必要字段：name")
        if "description" not in meta:
            errors.append("缺少必要字段：description")
    
    body = content[m.end():].strip() if m else content.strip()
    char_count = len(content)
    instruction_chars = len(body)
    
    status = "❌ 验证失败" if errors else "✅ 验证通过"
    
    lines = [
        f"━━━ SKILL.md 验证报告 ━━━",
        f"文件: {filepath}",
        f"状态: {status}",
        f"",
        f"📊 统计:",
        f"  总字符数: {char_count}",
        f"  预估 Token 数: ~{char_count // 4}",
        f"  指令内容: {instruction_chars} 字符",
        f"  元数据字段: {len(meta)} 个",
        f"  YAML 语法: {'✅ 正确' if m else '❌ 缺少 YAML 头'}",
    ]
    if errors:
        lines.append(f"")
        lines.append(f"⚠️ 问题：")
        for e in errors:
            lines.append(f"  • {e}")
    lines.append("━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _show_template(category="standard"):
    """显示 Skill 模板。"""
    templates = {
        "minimal": """---
name: my-skill
description: 简短描述
---

# 任务说明

## 步骤
1. 第一步
2. 第二步
""",
        "standard": """---
name: my-skill
description: >
  描述这个技能，包括何时使用它。
license: MIT
---

# 任务说明

## 核心原则
1. 原则一
2. 原则二

## 步骤
1. 第一步
2. 第二步

## 边界处理
| 场景 | 处理方式 |
|------|---------|
| ... | ... |
""",
        "code": """---
name: my-code-skill
description: >
  描述这个技能，包括何时使用它。
  例如：当用户需要进行数据分析时使用。
license: MIT
compatibility: python>=3.10, pip packages: pandas numpy
---

# 任务说明

## 步骤
1. 理解用户需求
2. 执行相关脚本
3. 返回结果

## 脚本使用说明
scripts/ 目录中包含可执行脚本，使用相对路径引用：

```python
from scripts.analyze import run_analysis
results = run_analysis(data)
```

## 参考文档
详细参考见 references/ 目录。

## 边界情况处理
- 输入为空时的处理
- 错误处理流程
""",
    }
    return templates.get(category, templates["standard"])


def _learn(topic="overview"):
    """输出学习资料。"""
    docs = {
        "overview": """━━━ Agent Skills 概述 ━━━

什么是 Agent Skill？
────────────────────
Agent Skill（技能包）是一种轻量级、开放格式的 AI Agent 能力扩展包。
核心思想：把专业知识和工作流程打包成可复用的技能包，
让 AI Agent 在需要时动态加载。

核心文件：SKILL.md（YAML 元数据 + Markdown 指令）

工作原理：渐进式披露（Progressive Disclosure）
  📋 发现阶段 → 只加载 name 和 description
  📖 激活阶段 → 加载完整 SKILL.md 指令
  🚀 执行阶段 → 按指令执行
""",
        "spec": """━━━ Skill 规范详解 ━━━

SKILL.md 结构：
---
name: my-skill          # 必填，唯一名称
description: ...        # 必填，简短描述
license: MIT            # 可选
compatibility: ...      # 可选
---

可选资源目录：
  scripts/    → 可执行脚本
  references/ → 参考文档
  assets/     → 静态资源
""",
        "best-practices": """━━━ 最佳实践 ━━━

1. 角色锚定：给 Skill 一个鲜明的角色
2. 哲学先行：定义 3-5 条核心原则
3. 多技能模块：拆成 3-6 个原子技能
4. 流程剧本化：步骤编号 + 动词开头
5. 边界清单：表格列出异常场景
6. 输出示例：让 Agent 知道输出格式
""",
    }
    return docs.get(topic, docs["overview"])


# ============================================================
# 新增：自动加载功能
# ============================================================

def _auto_discover():
    """自动发现所有 Skill。"""
    global _SKILL_INDEX
    _SKILL_INDEX = _scan_skills()
    if not _SKILL_INDEX:
        return "📭 未找到任何 Skill"
    lines = [f"📋 共发现 {len(_SKILL_INDEX)} 个 Skill：\n"]
    for name, info in sorted(_SKILL_INDEX.items()):
        desc = info["description"][:80] + "..." if len(info["description"]) > 80 else info["description"]
        lines.append(f"  • {name}")
        lines.append(f"    📝 {desc}")
    return "\n".join(lines)


def _auto_match(query, top_k=2):
    """智能匹配 Skill。"""
    global _SKILL_INDEX
    if not _SKILL_INDEX:
        _SKILL_INDEX = _scan_skills()
    if not _SKILL_INDEX:
        return "📭 没有可用的 Skill。"
    
    scored = []
    for name, info in _SKILL_INDEX.items():
        score = _compute_relevance(query, name, info["description"])
        if score > 0:
            scored.append((score, name, info))
    scored.sort(key=lambda x: x[0], reverse=True)
    
    if not scored:
        return "🤷 未找到匹配的 Skill。"
    
    lines = [f"🎯 根据「{query}」匹配到：\n"]
    for i, (score, name, info) in enumerate(scored[:top_k], 1):
        desc = info["description"][:100] + "..." if len(info["description"]) > 100 else info["description"]
        lines.append(f"  #{i} [{score:.2f}] {name}")
        lines.append(f"     {desc}")
    lines.append(f"\n💡 用 skill_load(name) 加载完整指令。")
    return "\n".join(lines)


def _auto_load(skill_name):
    """加载并激活 Skill。"""
    global _SKILL_INDEX, _ACTIVE_SKILLS
    if not _SKILL_INDEX:
        _SKILL_INDEX = _scan_skills()
    
    matched = _SKILL_INDEX.get(skill_name)
    if not matched:
        for name in _SKILL_INDEX:
            if name.startswith(skill_name) or skill_name in name:
                matched = _SKILL_INDEX[name]
                break
    
    if not matched:
        avail = ", ".join(sorted(_SKILL_INDEX.keys())) if _SKILL_INDEX else "无"
        return f"❌ 未找到「{skill_name}」。可用：{avail}"
    
    name = matched["name"]
    content = matched["content"]
    desc = matched["description"][:100] + "..." if len(matched["description"]) > 100 else matched["description"]
    
    return (
        f"✅ 已加载并激活 Skill：**{name}**\n"
        f"📝 {desc}\n"
        f"📄 指令长度：{len(content)} 字符\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{content}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 此 Skill 已激活，后续将遵循其指令。"
    )


def _auto_context():
    """查看活跃 Skill 上下文。"""
    if not _ACTIVE_SKILLS:
        return "📭 当前没有激活的 Skill。"
    lines = ["🔌 当前活跃的 Skill：\n"]
    for name, info in _ACTIVE_SKILLS.items():
        content = info.get("full_instructions", "")
        preview = content[:200].replace('\n', ' ') + "..." if len(content) > 200 else content.replace('\n', ' ')
        lines.append(f"  • {name}（{info.get('loaded_at', '')}）")
        lines.append(f"    预览：{preview}")
    lines.append(f"\n💡 共 {len(_ACTIVE_SKILLS)} 个活跃 Skill。")
    return "\n".join(lines)


def _auto_reload():
    """刷新索引。"""
    global _SKILL_INDEX
    old = len(_SKILL_INDEX)
    _SKILL_INDEX = _scan_skills()
    new = len(_SKILL_INDEX)
    return f"🔄 已重新扫描。之前 {old} 个 Skill，现在 {new} 个。"


# ============================================================
# 工具描述（保留原有6个 + 新增5个 = 11个）
# ============================================================
TOOLS = [
    # --- 原有工具 ---
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "列出 skills/ 目录下所有 Skill（包含 SKILL.md 的子目录），返回每个 Skill 的名称、描述和路径。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_show",
            "description": "查看指定 Skill 的 SKILL.md 完整内容，包括元数据和指令。支持名称、路径或前缀匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_path": {"type": "string", "description": "Skill 目录路径或名称"},
                    "skills_base": {"type": "string", "description": "Skills 根目录"}
                },
                "required": ["skill_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_create",
            "description": "创建一个符合 Agent Skills 开放标准的 Skill（技能包）。生成 SKILL.md 文件及可选目录结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名称"},
                    "description": {"type": "string", "description": "技能描述"},
                    "instructions": {"type": "string", "description": "Markdown 格式的完整指令"},
                    "output_dir": {"type": "string", "description": "输出目录"},
                    "license": {"type": "string", "description": "许可证"},
                    "compatibility": {"type": "string", "description": "兼容性说明"},
                    "create_scripts_dir": {"type": "boolean", "description": "是否创建 scripts/ 目录"},
                    "create_references_dir": {"type": "boolean", "description": "是否创建 references/ 目录"},
                    "create_assets_dir": {"type": "boolean", "description": "是否创建 assets/ 目录"}
                },
                "required": ["name", "description", "instructions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_validate",
            "description": "验证一个 SKILL.md 文件是否符合 Agent Skills 开放标准规范。检查名称、描述、YAML 格式等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "SKILL.md 文件路径"}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_template",
            "description": "生成一个 Skill 模板（SKILL.md 内容模板），帮助用户快速开始编写自己的技能。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "模板类别",
                        "enum": ["minimal", "standard", "code"]
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_learn",
            "description": "输出 Agent Skills 的学习资料：什么是 Skill、工作原理、最佳实践等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "学习主题",
                        "enum": ["overview", "spec", "best-practices"]
                    }
                },
                "required": []
            }
        }
    },
    # --- 新增自动加载工具 ---
    {
        "type": "function",
        "function": {
            "name": "skill_auto_discover",
            "description": "扫描 skills/ 目录下所有 Skill（含 SKILL.md 的子目录），返回每个 Skill 的名称和描述。适用于启动时了解可用能力。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_auto_match",
            "description": "根据用户输入的文本，智能匹配最相关的 Skill。返回匹配得分和推荐。适用于不确定该用哪个 Skill 时先用此工具探测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户输入或自然语言描述的任务需求"},
                    "top_k": {"type": "integer", "description": "返回前几个匹配结果，默认 2"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_auto_load",
            "description": "加载指定 Skill 的完整 SKILL.md 指令内容，并标记为活跃状态。加载后 Agent 将遵循该 Skill 的指令行事。支持名称前缀模糊匹配（如 'nvwa' 匹配 'nvwa-skill'）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Skill 名称，支持前缀匹配"}
                },
                "required": ["skill_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_auto_context",
            "description": "查看当前已加载（激活）的 Skill 上下文。显示每个活跃 Skill 的名称和指令预览。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_auto_reload",
            "description": "重新扫描 skills/ 目录，刷新所有 Skill 索引。添加/删除 Skill 后需要调用此工具更新缓存。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_unload",
            "description": "卸载已激活的 Skill，从系统提示中移除其指令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "要卸载的 Skill 名称"}
                },
                "required": ["skill_name"]
            }
        }
    },
]


def execute(name, args):
    """根据 name 分发到对应的处理函数。"""
    dispatch = {
        "skill_list": lambda: _list_skills(args.get("target_dir")),
        "skill_show": lambda: _show_skill(args.get("skill_path"), args.get("skills_base")),
        "skill_create": lambda: _create_skill(
            args.get("name"), args.get("description"), args.get("instructions"),
            args.get("output_dir"), args.get("license"), args.get("compatibility"),
            args.get("create_scripts_dir", False), args.get("create_references_dir", False),
            args.get("create_assets_dir", False)
        ),
        "skill_validate": lambda: _validate_skill(args.get("filepath")),
        "skill_template": lambda: _show_template(args.get("category", "standard")),
        "skill_learn": lambda: _learn(args.get("topic", "overview")),
        "skill_auto_discover": _auto_discover,
        "skill_auto_match": lambda: _auto_match(args.get("query", ""), args.get("top_k", 2)),
        "skill_auto_load": lambda: _auto_load(args.get("skill_name", "")),
        "skill_auto_context": _auto_context,
        "skill_auto_reload": _auto_reload,
        "skill_unload": lambda: f'✅ Skill「{args.get("skill_name", "")}」的指令已从系统提示中移除',
    }
    handler = dispatch.get(name)
    if handler:
        return handler()
    return f"未知命令：{name}"
