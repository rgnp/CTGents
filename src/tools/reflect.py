"""失败反思 — 工具调用失败后自动记录模式，下次 LLM 看到后自然避免踩坑。

不调 LLM，纯文件存储。失败数据自动注入系统上下文，
LLM 看到"上次这个工具这个参数失败了"就知道检查什么。
"""

import os
from datetime import datetime
from pathlib import Path

REFLECT_DIR = Path(os.path.expanduser("~")) / ".ctgents" / "reflections"
MAX_PER_TOOL = 5  # 每个工具最多保留条数


def record_failure(tool_name: str, args: dict, error: str, result_preview: str = "") -> None:
    """记录一次工具调用失败。"""
    REFLECT_DIR.mkdir(parents=True, exist_ok=True)
    key = tool_name.replace("_", "-")
    fp = REFLECT_DIR / f"{key}.txt"

    ts = datetime.now().strftime("%m-%d %H:%M")
    arg_keys = ", ".join(sorted(args.keys()))
    error_short = error[:80] if error else "未知错误"

    line = f"[{ts}] tool={tool_name} args=({arg_keys}) error={error_short}"

    lines = []
    if fp.exists():
        raw = fp.read_text(encoding="utf-8").strip()
        if raw:
            lines = raw.split("\n")

    lines.append(line)

    # 只保留最近 N 条
    if len(lines) > MAX_PER_TOOL:
        lines = lines[-MAX_PER_TOOL:]

    fp.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_failures(tool_name: str) -> str | None:
    """获取某工具的最近失败记录。"""
    key = tool_name.replace("_", "-")
    fp = REFLECT_DIR / f"{key}.txt"
    return fp.read_text(encoding="utf-8").strip() if fp.exists() else None


def get_all_failures() -> list[tuple[str, str]]:
    """返回 (tool_name, content) 列表，按工具名排序。"""
    if not REFLECT_DIR.exists():
        return []
    result = []
    for fp in sorted(REFLECT_DIR.glob("*.txt")):
        tool = fp.stem.replace("-", "_")
        content = fp.read_text(encoding="utf-8").strip()
        if content:
            result.append((tool, content))
    return result


def get_summary() -> str | None:
    """返回所有失败记录的摘要，供注入系统上下文使用。"""
    all_fails = get_all_failures()
    if not all_fails:
        return None

    parts = ["## 近期工具失败记录（供参考，避免重复踩坑）"]
    for tool, content in all_fails:
        lines = content.split("\n")
        last = lines[-1][:80]
        count = len(lines)
        parts.append(f"- {tool}: {last} ({count}次)")
    return "\n".join(parts)
