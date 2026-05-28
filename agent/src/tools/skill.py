"""Skill 发现工具——扫描 skills/ 目录，输出 name + description。"""

import re
from pathlib import Path

TOOLS_SKILL = [
    {
        "type": "function",
        "function": {
            "name": "skill_discover",
            "description": (
                "扫描 skills/ 目录，列出所有可用 Skill 的名称和描述（渐进式披露的第一步）。"
                "每个 Skill 只需 ~100 token 即可了解其用途。"
                "启动时或接到新任务时，先调用此工具看看有没有相关 Skill 可以用。"
                "匹配到相关 Skill 后，用 read_file 读 SKILL.md 获取完整指令。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def skill_discover() -> str:
    """扫描 skills/ 目录下所有 SKILL.md，提取 name + description。"""
    skills_dir = Path("skills")
    if not skills_dir.exists():
        return "skills/ 目录不存在，尚无任何 Skill。上网学习后用 skill_create 创建。"

    found: list[dict[str, str]] = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir():
            continue
        skill_md = item / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue

        # 提取 YAML frontmatter 中的 name 和 description
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not match:
            continue
        name = ""
        desc = ""
        for line in match.group(1).split("\n"):
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                # 多行描述：> 后的缩进行
                if desc == ">" or desc == "|":
                    desc = ""

        # 从 body 里补描述（description: > 风格）
        if not desc:
            body_start = match.end() + 1
            body = text[body_start:].strip()
            desc = body[:120].replace("\n", " ") + ("..." if len(body) > 120 else "")

        found.append({
            "name": name or item.name,
            "description": desc or "（无描述）",
        })

    if not found:
        return "skills/ 下未找到有效的 SKILL.md。"

    lines = [f"skills/ 目录中共 {len(found)} 个可用 Skill：\n"]
    for s in found:
        lines.append(f"  {s['name']}")
        lines.append(f"    {s['description']}")
        lines.append("")
    return "\n".join(lines).strip()
