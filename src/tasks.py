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
AMBITIONS_FILE = TASKS_DIR / "ambitions.md"
ARCHIVE_DIR = TASKS_DIR / "archive"
# 步骤标记：[ ] 未做 / [o] 进行中 / [x] 完成。含前两者即"未完成"。
_UNFINISHED_MARKERS = ("[ ]", "[o]", "[O]")
_SLUG_FALLBACK = "task"
# 方向发现缓存：同会话只跑一次（~5s），不进每轮循环
_gaps_reported = False


def reset_gaps_cache() -> None:
    """新会话开始时重置，允许再次上报方向发现。"""
    global _gaps_reported
    _gaps_reported = False


def read_ambitions() -> str:
    """返回 ambitions.md 中自己填写的野心内容（跳过模板说明）。"""
    if not AMBITIONS_FILE.exists():
        return ""
    text = AMBITIONS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    # 只取"## 当前野心"后的内容（跳过模板说明）
    marker = "## 当前野心"
    idx = text.find(marker)
    if idx == -1:
        return ""
    body = text[idx + len(marker):].strip()
    if not body:
        return ""
    return body


def has_ambitions() -> bool:
    """ambitions.md 存在且 agent 自己写了东西。"""
    return bool(read_ambitions())


def get_task_progress_line() -> str:
    """解析 current.md 步骤，返回一行进度，如 "📋 (2/5) ✅ S1 ✅ S2 🔄 S3 ⬜ S4"。

    无任务或无法解析返回空串。
    """
    text = read_current()
    if not text:
        return ""
    steps: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            steps.append(("✅", stripped[5:].strip()))
        elif stripped.startswith("- [o]") or stripped.startswith("- [O]"):
            steps.append(("🔄", stripped[5:].strip()))
        elif stripped.startswith("- [ ]"):
            steps.append(("⬜", stripped[5:].strip()))
        elif stripped.startswith("- [r]"):
            steps.append(("🔁", stripped[5:].strip()))
    if not steps:
        return ""
    done = sum(1 for s, _ in steps if s == "✅")
    total = len(steps)
    labels = [f"{s} {lbl[:30]}" for s, lbl in steps]
    progress = f"📋 ({done}/{total}) " + " ".join(labels)
    if len(progress) > 200:
        progress = f"📋 ({done}/{total}) " + " ".join(labels[:4])
        if len(labels) > 4:
            progress += f" …(+{len(labels) - 4})"
    return progress


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
    """生成 volatile 上下文消息：方向发现 + 未完成长任务 + 被动进化反思。"""
    global _gaps_reported
    parts: list[str] = []

    # ── 会话启动一次性检查：门通行证审计 + 方向发现（~5s）──
    if not _gaps_reported:
        _gaps_reported = True
        from .gate_audit import head_gate_notice
        gate_notice = head_gate_notice()
        if gate_notice:
            parts.append(gate_notice)
        from .gaps import detect_all_gaps as _detect_gaps
        from .gaps import format_gap_report as _fmt_gaps
        gap_report = _detect_gaps(top_n=5)
        gap_text = _fmt_gaps(gap_report)
        if gap_text:
            parts.append(gap_text)

    # ── 野心清单（自己发现的事，不是任务）──
    if has_ambitions():
        parts.append(
            "📋 你之前记下了一些想做的事（tasks/ambitions.md），"
            "什么时候推进你自己判断：\n\n" + read_ambitions()
        )

    # ── 未完成长任务 ──
    if has_unfinished():
        parts.append(
            "⚠️ 你有一个未完成的长任务（tasks/current.md），上次没做完。"
            "请从未完成步骤（[ ] / [o]）的断点继续，不要从头重来；"
            "在步骤旁记录细进度（如 47/250），完成后按 AGENTS.md 清空并归档。\n\n"
            + read_current()
        )

    # ── 被动进化反思（含代码感知诊断）──
    from .diagnostics import diagnose_anomalies
    from .tracker import get_latest_reflections as _get_reflections
    reflections = _get_reflections(limit=3)
    if reflections:
        seen: set[tuple[str, str]] = set()
        anomalies: list[dict] = []
        for ref in reflections:
            for a in ref.get("anomalies", []):
                key = (a.get("tool", ""), a.get("type", ""))
                if key not in seen:
                    seen.add(key)
                    anomalies.append(a)

        if anomalies:
            diagnostics = diagnose_anomalies(anomalies)
            lines = ["🔍 被动进化发现了以下值得关注的问题："]
            for a, d in zip(anomalies, diagnostics, strict=True):
                icon = {"crit": "🔴", "warn": "🟡"}.get(a.get("severity", ""), "⚪")
                lines.append(f"  {icon} [{d.anomaly_type}] {d.anomaly_detail}")
                if d.root_pattern != "unknown" or d.suggested_action:
                    lines.append(f"     → 诊断: {d.likely_cause}")
                    if d.suggested_action:
                        lines.append(f"     → 建议: {d.suggested_action}")
            lines.append(
                "如果需要修复，可以说 '处理这些' 或 '看看第一个'。"
                "我会用 /evolve 机制分析、修改、测试、提交。"
            )
            parts.append("\n".join(lines))

    if not parts:
        return None
    # _task_ctx: run_conversation 每轮按此标记剥旧再追加新——没有它剥除空转，
    # 任务上下文每轮堆一份副本(全在挂尾区,每个请求重算,白烧缓存 miss)。
    return {"role": "system", "content": "\n\n".join(parts),
            "_volatile": True, "_task_ctx": True}


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
