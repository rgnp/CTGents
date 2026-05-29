"""Skill 上下文管理：发现、激活、卸载。遵循 Agent Skills 开放标准的渐进式披露。"""

import re
from pathlib import Path


SKILLS_DIR = Path("skills")
_SKILL_TAG = "_skill"  # system message 标记


# ── 发现 ──


def scan_skills() -> dict[str, dict]:
    """扫描 skills/ 目录，返回 {name: {description, path, content}}。"""
    result: dict[str, dict] = {}
    if not SKILLS_DIR.is_dir():
        return result

    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        sf = entry / "SKILL.md"
        if not sf.is_file():
            continue
        try:
            content = sf.read_text(encoding="utf-8")
            meta = _parse_frontmatter(content)
            name = meta.get("name", entry.name)
            desc = meta.get("description", "")
            result[name] = {
                "description": desc,
                "path": str(entry),
                "content": content,
            }
        except Exception:
            continue
    return result


def make_discovery_context() -> dict | None:
    """生成发现层系统消息：所有 skill 的 name + description。"""
    skills = scan_skills()
    if not skills:
        return None

    lines = ["可用 Skill（需要时用 /skill load <名称> 加载）："]
    for name, info in sorted(skills.items()):
        desc = info["description"]
        # description 只取第一句
        short = desc.split("。")[0].split("\n")[0][:80] if desc else ""
        lines.append(f"  {name} — {short}")

    return {"role": "system", "content": "\n".join(lines), "_volatile": True}


def get_skill_content(name: str) -> str | None:
    """获取指定 skill 的完整 SKILL.md 内容。"""
    skills = scan_skills()
    info = skills.get(name)
    if not info:
        # 前缀模糊匹配
        for sname, sinfo in skills.items():
            if sname.startswith(name) or name in sname:
                info = sinfo
                break
    return info["content"] if info else None


def get_skill_name(name: str) -> str | None:
    """模糊匹配 skill 的正式名称。"""
    skills = scan_skills()
    if name in skills:
        return name
    for sname in skills:
        if sname.startswith(name) or name in sname:
            return sname
    return None


# ── 激活 / 卸载 ──


def activate_skill(messages: list[dict], name: str, content: str) -> str:
    """将 SKILL.md 内容注入为 system message。返回状态消息。"""
    # 先卸载同名旧版本
    deactivate_skill(messages, name)

    messages.append({
        "role": "system",
        "content": content,
        _SKILL_TAG: name,
    })
    return f"已加载 Skill: {name}（{len(content)} 字符）"


def deactivate_skill(messages: list[dict], name: str) -> str:
    """移除指定 skill 的 system message。"""
    before = len(messages)
    messages[:] = [m for m in messages if m.get(_SKILL_TAG) != name]
    removed = before - len(messages)
    return f"已卸载 Skill: {name}" if removed else f"Skill 未激活: {name}"


def list_active_skills(messages: list[dict]) -> list[str]:
    """列出当前激活的 skill 名称。"""
    seen: set[str] = set()
    for m in messages:
        tag = m.get(_SKILL_TAG)
        if tag:
            seen.add(tag)
    return sorted(seen)


def auto_match_and_load(messages: list[dict], user_input: str) -> str:
    """根据用户输入自动匹配并加载 Skill。返回状态消息。"""
    skills = scan_skills()
    if not skills:
        return ""

    active = set(list_active_skills(messages))
    inp_words = set(_tokenize(user_input))

    loaded: list[str] = []
    for name, info in skills.items():
        if name in active:
            continue
        desc_words = set(_tokenize(info["description"]))
        if not inp_words or not desc_words:
            continue
        overlap = inp_words & desc_words
        score = len(overlap) / min(len(inp_words), len(desc_words))
        if score >= 0.15:
            activate_skill(messages, name, info["content"])
            loaded.append(name)

    if loaded:
        return f"自动加载 Skill: {', '.join(loaded)}"
    return ""


def _tokenize(text: str) -> list[str]:
    """分词：CJK 单字 + ASCII 单词，过滤标点。"""
    tokens: list[str] = []
    t = text.lower()
    # CJK 字符逐字
    for ch in t:
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            tokens.append(ch)
    # ASCII 单词
    tokens.extend(re.findall(r'[a-z0-9]{2,}', t))
    return tokens


# ── 辅助 ──


def _parse_frontmatter(content: str) -> dict[str, str]:
    """解析 SKILL.md 的 YAML frontmatter。"""
    match = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', content, re.DOTALL)
    if not match:
        return {}
    meta: dict[str, str] = {}
    current_key = None
    current_lines: list[str] = []

    for line in match.group(1).split("\n"):
        if re.match(r'^[ \t]', line):  # 续行
            stripped = line.strip()
            if stripped and current_key:
                current_lines.append(stripped)
            continue
        # 新 key
        if current_key is not None:
            meta[current_key] = " ".join(current_lines) if current_lines else ""
        if ":" in line:
            key, _, val = line.partition(":")
            current_key = key.strip()
            val = val.strip()
            # YAML 折叠块 > 和字面块 | 的处理
            if val in (">", "|"):
                current_lines = []
            else:
                current_lines = [val] if val else []
        else:
            current_key = None
            current_lines = []

    if current_key is not None:
        meta[current_key] = " ".join(current_lines) if current_lines else ""
    return meta
