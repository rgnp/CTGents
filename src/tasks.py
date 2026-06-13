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
# 步骤标记
_UNFINISHED_MARKERS = ("[ ]", "[o]", "[O]")  # 活跃未完成（has_unfinished 判活用）
_BLOCKED_MARKERS = ("[r]", "[R]", "[!]")     # 阻塞/需重试（不触发续做注入，但不算全完成）
_ALL_NOT_DONE = _UNFINISHED_MARKERS + _BLOCKED_MARKERS  # 全完成判定用
_DONE_MARKERS = ("[x]", "[X]")               # 已完成步骤（is_all_done 要求至少一个）
_SLUG_FALLBACK = "task"
_ANCHOR_HEADING = "# 目标锚点"
# 方向发现缓存：同会话只跑一次（~5s），不进每轮循环
_gaps_reported = False
# 建任务建议：同会话只提示一次，避免每轮唠叨
_task_suggested = False


def reset_gaps_cache() -> None:
    """新会话开始时重置会话级一次性缓存（方向发现 + 建任务建议）。"""
    global _gaps_reported, _task_suggested
    _gaps_reported = False
    _task_suggested = False


def maybe_suggest_task_nudge(requests_made: int, threshold: int) -> str | None:
    """干了不少活（requests_made>=threshold）却没有 current.md 任务 → 一次性建议建任务。

    触发是【事实】（这一轮的请求数 + 没有任务文件），可机械判定、无假阳性；
    '这到底算不算需要跟踪的长任务' 是【判断】，连同 opt-out 一起留给 agent——
    不机械分类长任务（那会假阳性，见记忆 rule-placement）。同会话只提示一次。
    """
    global _task_suggested
    if _task_suggested or requests_made < threshold:
        return None
    if read_current().strip():  # 已有任务在跟踪，无需建议
        return None
    _task_suggested = True
    return (
        "[任务建议] 这一轮做了不少步，且当前没有 tasks/current.md 任务记录。"
        "如果这是个跨多步、可能跨会话的长任务，用 create_task 写下目标锚点 + 步骤清单——"
        "之后断点能自动续做、换会话也接得上；若马上就收尾，忽略本提示即可。"
    )


def read_ambitions() -> str:
    """返回 ambitions.md 全文（去掉一级标题）。"""
    if not AMBITIONS_FILE.exists():
        return ""
    text = AMBITIONS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    # 跳过一级标题行
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    # 两个分区都空 = 没有实质内容
    if not body or body == "## 你的目标\n\n_暂无。_\n\n## Agent 的目标\n\n_暂无。_":
        return ""
    return body


def has_ambitions() -> bool:
    """ambitions.md 存在且有实质目标。"""
    return bool(read_ambitions())


def _extract_anchor(text: str) -> str:
    """从 current.md 内容提取目标锚点：`# 目标锚点` 后到下一个空行或标题为止。"""
    lines = text.splitlines()
    in_anchor = False
    anchor_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if in_anchor:
            if not stripped or stripped.startswith("#"):
                break
            anchor_lines.append(stripped)
        elif stripped == _ANCHOR_HEADING:
            in_anchor = True
    return " ".join(anchor_lines).strip()


def get_task_progress_line() -> str:
    """解析 current.md 步骤，返回一行进度，如 "📋 (2/5) ✅ S1 ✅ S2 🔄 S3 ⬜ S4"。"""
    steps = _parse_task_steps()
    if not steps:
        return ""
    done = sum(1 for s, _ in steps if s == "✅")
    total = len(steps)
    labels = [f"{s} {lbl[:30]}" for s, lbl in steps]
    progress = f"📋 ({done}/{total}) " + " ".join(labels)
    return _trim_progress(progress, labels, done, total)


def _parse_task_steps() -> list[tuple[str, str]]:
    """解析 current.md 步骤行，返回 (图标, 文本) 列表。"""
    text = read_current()
    if not text:
        return []
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
    return steps


def _trim_progress(progress: str, labels: list, done: int, total: int) -> str:
    """超过 200 字符时截断进度线，保留前 4 步 + 省略标记。"""
    if len(progress) <= 200:
        return progress
    short = f"📋 ({done}/{total}) " + " ".join(labels[:4])
    if len(labels) > 4:
        short += f" …(+{len(labels) - 4})"
    return short


def read_current() -> str:
    """返回 current.md 内容（已 strip）；不存在返回空串。"""
    if not CURRENT_TASK_FILE.exists():
        return ""
    return CURRENT_TASK_FILE.read_text(encoding="utf-8").strip()


def has_unfinished() -> bool:
    """current.md 存在且含活跃未完成步骤（[ ] / [o]）。

    [r]（需重试）和 [!]（阻塞）不算"活跃未完成"——
    有这些标记时 agent 不需要自动续做，但也不算全完成。
    """
    text = read_current()
    return bool(text) and any(marker in text for marker in _UNFINISHED_MARKERS)


def is_all_done() -> bool:
    """current.md 存在、且是一个所有步骤都完成（全 [x]）的任务。

    有 [ ]/[o]/[r]/[!] 任一即 False；**还要求至少一个 [x]**——否则像
    "（无进行中的任务）"这种无步骤的占位文本会被误判成"全完成"而触发多余归档。
    """
    text = read_current()
    if not text:
        return False
    has_done = any(m in text for m in _DONE_MARKERS)
    return has_done and not any(m in text for m in _ALL_NOT_DONE)


def make_task_context_message() -> dict | None:
    """生成 volatile 上下文消息：方向发现 + 长期目标 + 未完成长任务 + 被动进化反思。"""
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

    # ── 长期目标（你与 agent 共同的长期方向）──
    if has_ambitions():
        parts.append(
            "📋 你们共同的长期目标（tasks/ambitions.md），"
            "所有决策的弱方向参考：\n\n" + read_ambitions()
        )

    # ── 全完成自动归档（B 方案：下游兜底）──
    if is_all_done():
        result = archive_current()
        parts.append(
            "✅ 上次长任务所有步骤已完成，本次会话已自动归档。"
            f"（{result}）"
        )
    # ── 未完成长任务 ──
    elif has_unfinished():
        text = read_current()
        parts.append(
            "⚠️ 你有一个未完成的长任务（tasks/current.md），上次没做完。"
            "请从未完成步骤（[ ] / [o]）的断点继续，不要从头重来；"
            "在步骤旁记录细进度（如 47/250），完成后按 AGENTS.md 清空并归档。\n\n"
            + text
        )
        # ── 锚点对照：每轮提醒检查方向 ──
        anchor = _extract_anchor(text)
        if anchor:
            parts.insert(
                -1,  # 插在任务内容之前
                f"🎯 目标锚点：{anchor}\n"
                "↳ 每完成一个步骤，对照上方锚点检查当前方向：做的事还在解决这个问题吗？"
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


def create_task(content: str) -> str:
    """写入 current.md。必须有 # 目标锚点，拒绝写入否则漂移无绳。

    自动追加归档步骤（方案 A）。
    """
    final = content.strip()
    if _ANCHOR_HEADING not in final:
        return (
            "拒绝：缺少 # 目标锚点。\n"
            "请在任务内容中加入一行 '# 目标锚点' 和一句描述——"
            "说清这个任务到底要解决什么问题。\n"
            "例如：\n"
            "  # 目标锚点\n  让 current.md 从'旗'变成'绳'，每步自动对照方向。"
        )
    if "- [ ] 归档" not in final:
        final += "\n- [ ] 归档 current.md → tasks/archive/"
    CURRENT_TASK_FILE.write_text(final + "\n", encoding="utf-8")
    return "已写入 current.md（含目标锚点 + 自动归档步骤）。"


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
