"""会话摘要自动生成：从消息列表提取结构化信息，写入 knowledge/sessions/。

L1 层：每会话关断自动写。6 行固定模板，rag_index_research 可索引。
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "sessions"

# ── 标签映射：工具名 → 标签关键词 ──
_TOOL_TAG_MAP: dict[str, str] = {
    "write_file": "#修改", "edit_file_lines": "#修改",
    "git_commit": "#工程", "git_push": "#工程", "git_pr": "#工程",
    "git_review": "#工程", "git_status": "#工程",
    "search_web": "#调研", "learn": "#调研", "scan_papers": "#调研",
    "read_file": "#探查", "grep_code": "#探查", "rag_query": "#探查",
    "remember": "#记忆", "recall": "#记忆", "forget": "#记忆",
    "run_python": "#调试", "run_command": "#调试",
    "pin": "#决策", "rag_search": "#知识库",
}

# ── 文件后缀映射 ──
_EXT_TAG_MAP: dict[str, str] = {
    ".py": "#代码", ".md": "#文档", ".json": "#数据",
    ".yaml": "#配置", ".yml": "#配置", ".toml": "#配置",
}

# ── 决策指示词 ──
_DECISION_INDICATORS = [
    r"(应该|必须|不要|不许|禁止|坚持|只|仅)\S*",
    r"(改|做|用)\S+",
    r"(以后|从现在起|接下来|下一次)\S*",
]


def _extract_tags(messages: list[dict]) -> list[str]:
    """从对话中推断标签。"""
    tags: set[str] = set()
    _collect_tool_tags(messages, tags)
    _collect_file_ext_tags(messages, tags)
    _collect_misc_tags(messages, tags)
    return sorted(tags) or ["#对话"]


def _collect_tool_tags(messages: list[dict], tags: set[str]) -> None:
    """工具调用 → 标签（#修改/#工程/#调研 等）。"""
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tag = _TOOL_TAG_MAP.get(tc["function"]["name"])
                if tag:
                    tags.add(tag)


def _collect_file_ext_tags(messages: list[dict], tags: set[str]) -> None:
    """write_file 目标路径后缀 → 标签（#代码/#文档/#配置 等）。"""
    for m in messages:
        if m.get("role") != "tool" or m.get("_tool_name") != "write_file":
            continue
        content = m.get("content") or ""
        with contextlib.suppress(Exception):
            parsed = json.loads(content)
            path = parsed.get("path", "")
            for ext, tag in _EXT_TAG_MAP.items():
                if path.endswith(ext):
                    tags.add(tag)


def _collect_misc_tags(messages: list[dict], tags: set[str]) -> None:
    """检查记忆/压缩等特殊标签。"""
    if any("remember" in (m.get("content") or "") for m in messages
           if m.get("role") == "assistant"):
        tags.add("#记忆系统")
    if any("compact" in (m.get("content") or "").lower() or "压缩" in (m.get("content") or "")
           for m in messages if m.get("role") == "user"):
        tags.add("#压缩")


def _extract_actions(messages: list[dict]) -> list[str]:
    """提取主要动作：commit 消息 + 写文件目标。"""
    actions: list[str] = []
    seen: set[str] = set()

    for m in messages:
        c = (m.get("content") or "").strip()
        if not c:
            continue

        # git commit messages
        if m.get("role") == "tool" and m.get("_tool_name") == "git_commit":
            with contextlib.suppress(Exception):
                parsed = json.loads(c)
                msg = parsed.get("message") or parsed.get("result", "")
                if msg:
                    lines = msg.split("\n")
                    act = lines[0].strip().rstrip(".")
                    if act and act not in seen:
                        actions.append(act)
                        seen.add(act)

        # write_file targets
        if m.get("role") == "tool" and m.get("_tool_name") == "write_file":
            with contextlib.suppress(Exception):
                parsed = json.loads(c)
                path = parsed.get("path", "")
                short = _shorten_path(path)
                if short and short not in seen:
                    actions.append(f"写 {short}")
                    seen.add(short)

    if len(actions) > 5:
        actions = actions[:5]
        actions.append("…")
    return actions


def _extract_decisions(messages: list[dict]) -> list[str]:
    """提取关键决策：用户带有明确方向性的指令。"""
    decisions: list[str] = []

    user_msgs = [m for m in messages if m.get("role") == "user"]
    for m in user_msgs:
        c = (m.get("content") or "").strip()
        # 短指令且含决定词，排除问句
        if (len(c) < 120 and "?" not in c and "？" not in c
                and any(re.search(ind, c) for ind in _DECISION_INDICATORS)):
            decisions.append(c[:80])

    # 去重简写
    seen: set[str] = set()
    unique: list[str] = []
    for d in decisions:
        key = d[:30]
        if key not in seen:
            unique.append(d)
            seen.add(key)

    return unique[:4]


def _extract_feedback(messages: list[dict]) -> list[str]:
    """提取用户反馈：纠正、偏好、催促。"""
    feedback: list[str] = []

    for m in messages:
        if m.get("role") != "user":
            continue
        c = (m.get("content") or "").strip()
        # 纠正
        if re.search(r"(不对|错了|不是|不要|别).{0,30}", c):
            feedback.append(f"纠正: {c[:60]}")
        # 催促
        elif re.search(r"做完了吗|中断|继续", c):
            feedback.append("催促收尾")
        # 偏好
        elif re.search(r"(我喜欢|我希望|简洁|少|短|长)", c):
            feedback.append(f"偏好: {c[:60]}")

    return feedback[:3]


def _extract_products(messages: list[dict]) -> list[str]:
    """提取产物：commit hash。"""
    hashes: list[str] = []

    for m in messages:
        if m.get("role") != "tool":
            continue
        c = (m.get("content") or "")
        with contextlib.suppress(Exception):
            p = json.loads(c)
            h = p.get("hash") or p.get("commit") or ""
            if h and len(h) == 7 and h[0].isalnum():
                hashes.append(h)

    return hashes[:3]


def _shorten_path(path: str) -> str:
    """缩短路径：src/xxx.py 或 tests/xxx.py。"""
    for base in ("src", "tests"):
        idx = path.find(f"/{base}/")
        if idx == -1:
            idx = path.find(f"\\{base}\\")
        if idx != -1:
            return path[idx + 1:]
    return Path(path).name


def _slug_from_actions(actions: list[str]) -> str:
    """从动作中提取简短 slug。"""
    if not actions:
        return "session"

    first = actions[0]
    # commit 消息取第一个词
    if not first.startswith("写 "):
        words = first.split(":")[0].split()
        slug = "-".join(w.lower() for w in words[:2] if w.isalpha())
        if slug:
            return slug

    # 写文件 → 提取模块名
    if first.startswith("写 ") or first.startswith("改 "):
        p = Path(first[2:].strip())
        stem = p.stem
        return stem.lower()

    return "session"


def _make_summary(messages: list[dict], session_id: str | None = None) -> str:
    """从消息列表生成 L1 会话摘要 Markdown。"""
    tags = _extract_tags(messages)
    actions = _extract_actions(messages)
    decisions = _extract_decisions(messages)
    feedback = _extract_feedback(messages)
    products = _extract_products(messages)

    slug = _slug_from_actions(actions)
    date = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"# 会话 #{date}-{slug}",
        f"- 标签: {' '.join(tags)}",
    ]

    if actions:
        lines.append(f"- 做了什么: {'; '.join(actions)}")

    if decisions:
        lines.append(f"- 关键决策: {' | '.join(decisions)}")

    if feedback:
        lines.append(f"- 用户反馈: {'; '.join(feedback)}")

    if products:
        lines.append(f"- 产物: 提交 {' '.join(products)}")

    lines.append("")
    return "\n".join(lines)


def write_session_summary(messages: list[dict], session_id: str | None = None) -> str | None:
    """生成 L1 摘要并写入 knowledge/sessions/。返回写入的文件名。

    不发 LLM 调用——纯文本提取，确定性地快。
    """
    try:
        summary = _make_summary(messages, session_id)
    except Exception:
        return None

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d")
    slug = _slug_from_actions(_extract_actions(messages))
    filename = f"{date}-{slug}.md"
    path = KNOWLEDGE_DIR / filename

    # 避免覆盖同名文件（同一天同 slug）
    if path.exists():
        counter = 1
        while path.exists():
            filename = f"{date}-{slug}-{counter}.md"
            path = KNOWLEDGE_DIR / filename
            counter += 1

    path.write_text(summary, encoding="utf-8")
    return filename
