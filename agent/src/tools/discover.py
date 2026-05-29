"""统一能力发现——一次调用了解所有可用能力。"""

import re
from pathlib import Path


def discover_capabilities() -> str:
    """扫描所有能力目录，返回全景摘要。"""
    lines = []

    # ── 内置工具 ──
    builtin = [
        ("search_web",   "搜索互联网"),
        ("read_page",    "阅读网页全文"),
        ("read_file",    "读取本地文件"),
        ("write_file",   "写入文件"),
        ("list_files",   "浏览目录"),
        ("delete_file",  "删除文件"),
        ("run_python",   "执行 Python 代码"),
        ("grep_code",    "搜索代码"),
        ("think",        "思考与规划"),
        ("install_plugin", "安装新插件"),
    ]
    lines.append(f"内置工具 ({len(builtin)} 个)：")
    for name, desc in builtin:
        lines.append(f"  {name} — {desc}")
    lines.append("")

    # ── 插件 ──
    from .plugin_mgr import _plugins
    if _plugins:
        lines.append(f"已安装插件 ({len(_plugins)} 个)：")
        for pname, mod in _plugins.items():
            desc = getattr(mod, "DESCRIPTION", "（无描述）")
            tools = [t["function"]["name"] for t in getattr(mod, "TOOLS", [])]
            lines.append(f"  {pname} — {desc}")
            if tools:
                lines.append(f"    工具: {', '.join(tools)}")
        lines.append("")

    # ── Skill ──
    skills_dir = Path("skills")
    if skills_dir.exists():
        found = []
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
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            name = item.name
            desc = ""
            if match:
                for line in match.group(1).split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        val = line.split(":", 1)[1].strip()
                        if val not in (">", "|", ""):
                            desc = val
                if not desc:
                    body_start = match.end() + 1
                    body = text[body_start:].strip()
                    desc = body[:100].replace("\n", " ") + ("..." if len(body) > 100 else "")
            found.append((name, desc or "（无描述）"))

        if found:
            lines.append(f"可用 Skill ({len(found)} 个)，用 read_file 读全文：")
            for name, desc in found:
                lines.append(f"  {name} — {desc}")
            lines.append("")

    return "\n".join(lines).strip() if lines else "未找到任何能力"


def execute(name: str, args: dict) -> str | None:
    if name == "discover":
        return discover_capabilities()
    return None
