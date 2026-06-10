"""长任务状态：tasks/current.md 的读取 / 判活 / 注入 / 归档。

current.md 是长任务的"指令镜子" + 进度账本。一个长任务（如"搜 250 次论文"）
装不进单个上下文窗口，必须跨会话分块续做。本模块让启动时若有未完成步骤就把
current.md 注入上下文（volatile、缓存安全），agent 每次开会话都看得见断点，
从未完成处接着做，而不是把计划烂在文件里。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
CURRENT_TASK_FILE = TASKS_DIR / "current.md"
ARCHIVE_DIR = TASKS_DIR / "archive"

# 步骤标记：[ ] 未做 / [o] 进行中 / [x] 完成。含前两者即"未完成"。
_UNFINISHED_MARKERS = ("[ ]", "[o]", "[O]")
_SLUG_FALLBACK = "task"


def read_current() -> str:
    """返回 current.md 内容（已 strip）；不存在返回空串。"""
    if not CURRENT_TASK_FILE.exists():
        return ""
    return CURRENT_TASK_FILE.read_text(encoding="utf-8").strip()


def has_unfinished() -> bool:
    """current.md 存在且含未完成步骤（[ ] 或 [o]）。"""
    text = read_current()
    return bool(text) and any(marker in text for marker in _UNFINISHED_MARKERS)


def make_task_context_message() -> dict | None:
    """生成 volatile 上下文消息：未完成长任务 + 被动进化反思（如有）。"""
    parts: list[str] = []

    # ── 未完成长任务 ──
    if has_unfinished():
        parts.append(
            "⚠️ 你有一个未完成的长任务（tasks/current.md），上次没做完。"
            "请从未完成步骤（[ ] / [o]）的断点继续，不要从头重来；"
            "在步骤旁记录细进度（如 47/250），完成后按 AGENTS.md 清空并归档。\n\n"
            + read_current()
        )

    # ── 被动进化反思 ──
    from .tracker import get_latest_reflections as _get_reflections
    reflections = _get_reflections(limit=3)  # noqa: F811
    if reflections:
        ref_lines = ["🔍 被动进化发现了以下值得关注的问题："]
        for i, ref in enumerate(reflections, 1):
            for a in ref.get("anomalies", []):
                icon = {"crit": "🔴", "warn": "🟡"}.get(a.get("severity", ""), "⚪")
                ref_lines.append(
                    f"  {icon} [{a.get('type', '?')}] {a.get('detail', '')}"
                )
            if i >= 2:
                break
        ref_lines.append(
            "如果需要修复，可以说 '处理这些' 或 '看看第一个'。"
            "我会用 /evolve 机制分析、修改、测试、提交。"
        )
        parts.append("\n".join(ref_lines))

    if not parts:
        return None
    return {"role": "system", "content": "\n\n".join(parts), "_volatile": True}


def _derive_slug(text: str) -> str:
    """从首个 Markdown 标题派生归档用 slug；取不到用 fallback。"""
    for line in text.splitlines():
        stripped = line.lstrip("# ").strip()
        if line.startswith("#") and stripped:
            slug = re.sub(r"[^\w一-鿿]+", "-", stripped).strip("-")
            return slug or _SLUG_FALLBACK
    return _SLUG_FALLBACK


def archive_current(slug: str = "") -> str:
    text = read_current()
    if not text:
        return "current.md 为空，无可归档。"
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    safe_slug = re.sub(r"[^\w一-鿿-]+", "-", slug).strip("-") or _derive_slug(text)
    dest = ARCHIVE_DIR / f"{date}-{safe_slug}.md"
    dest.write_text(text + "\n", encoding="utf-8")
    CURRENT_TASK_FILE.write_text("", encoding="utf-8")
    return f"已归档 → tasks/archive/{dest.name}，current.md 已清空。"


def clear_current() -> str:
    """清空 current.md（不归档，用于放弃任务）。"""
    if not read_current():
        return "current.md 已是空的。"
    CURRENT_TASK_FILE.write_text("", encoding="utf-8")
    return "current.md 已清空（未归档）。"
