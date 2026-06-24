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
# 经验检索：同会话只检索一次，避免重复注入
_experience_reported = False


def reset_gaps_cache() -> None:
    """新会话开始时重置会话级一次性缓存（方向发现 + 建任务建议 + 经验检索）。"""
    global _gaps_reported, _task_suggested, _experience_reported
    _gaps_reported = False
    _task_suggested = False
    _experience_reported = False


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
    """从 current.md 提取目标锚点：`# 目标锚点` 后第一行非空文本。"""
    lines = text.splitlines()
    in_anchor = False
    for line in lines:
        stripped = line.strip()
        if in_anchor:
            if stripped and not stripped.startswith("-") and not stripped.startswith("#"):
                return stripped
            break
        elif stripped == _ANCHOR_HEADING:
            in_anchor = True
    return "".strip()


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


def resume_reminder() -> str | None:
    """会话首轮若有未完成长任务，返回一段恢复提醒（含目标锚点 + 任务清单），否则 None。

    治"可恢复"：current.md 持久化、会话日志每轮 autosave——状态从不丢，但跨会话/重启后
    agent 对"有个没做完的任务"没有注意力。main 在会话首轮 append-only 注入这一条（永久 log、
    非挂尾），让重开就能从断点接着干。一次性注入（不每轮 bloat），见 main.run_agent_turn。
    """
    if not has_unfinished():
        return None
    text = read_current()
    head = ("⏸ 你有一个未完成的长任务（tasks/current.md），从未完成步骤（[ ]/[o]）的断点继续，"
            "不要从头重来；在步骤旁记录细进度（如 47/250），完成后清空归档。")
    anchor = _extract_anchor(text)
    if anchor:
        head += f"\n🎯 目标锚点：{anchor}（每完成一步对照检查方向）"
    return head + "\n\n" + text


def make_task_context_message() -> dict | None:
    """生成 volatile 上下文消息：经验检索 + 方向发现 + 长期目标 + 未完成长任务 + 被动进化反思。"""
    global _gaps_reported, _experience_reported
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

    # 长期目标已移入缓存前缀（main._make_ambitions_message）——它 session 稳定，
    # 留在挂尾会让"对话"不再是输入结束位置、轮首缓存被淘汰整段重 miss（见 llm.py 注释）。

    # ── 经验检索：有未完成任务时，搜索相似历史任务注入教训 ──
    if not _experience_reported and has_unfinished():
        _experience_reported = True
        from .experience import format_experience_context, search_similar_tasks
        task_text = read_current()
        similar = search_similar_tasks(task_text, top_k=3)
        exp_ctx = format_experience_context(similar)
        if exp_ctx:
            parts.append(exp_ctx)

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


def update_plan(content: str) -> str:
    """覆盖更新 current.md（agent 动态重规划用）。必须有 # 目标锚点。

    与 create_task 不同：不自动追加归档步骤（已在计划中的步骤不动）。
    让 agent 可以增删改步骤、重排序、更新描述。
    """
    final = content.strip()
    if _ANCHOR_HEADING not in final:
        return (
            "拒绝：缺少 # 目标锚点。\n"
            "更新计划必须保留目标锚点——它是方向之绳。\n"
            "请加入 '# 目标锚点' 一行和一句描述。"
        )
    CURRENT_TASK_FILE.write_text(final + "\n", encoding="utf-8")
    done, total = count_steps(final)
    return f"✅ 计划已更新（{done}/{total} 步骤完成）:\n{final}"


def count_steps(text: str) -> tuple[int, int]:
    """统计 current.md 内容中的已完成/总步骤数。返回 (done, total)。"""
    done = 0
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            done += 1
            total += 1
        elif stripped.startswith(("- [ ]", "- [o]", "- [r]", "- [!]")):
            total += 1
    return done, total


def get_task_short_status() -> str:
    """返回简练的任务状态行，给状态栏用。如 '📋 任务名 (2/5)'。"""
    text = read_current()
    if not text:
        return ""
    done, total = count_steps(text)
    # 取标题（首个 # 或 ## 行）
    title = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("# 目标锚点"):
            title = s[2:].strip()
            break
        if s.startswith("## ") and title == "":
            title = s[3:].strip()
    if not title:
        title = "任务"
    if total == 0:
        return f"📋 {title}"
    return f"📋 {title} ({done}/{total})"


def read_current_active_step() -> str | None:
    """任务切片：解析 current.md，只返回当前第一个活跃的步骤和其子描述。

    避免把做完的 [x] 和整个长列表全喂给模型。
    返回格式：步骤行 + 紧跟的子描述。无未完成步骤时返回 None。
    """
    text = read_current()
    if not text:
        return None

    active_lines: list[str] = []
    found_active = False

    for line in text.splitlines():
        stripped = line.strip()
        # 遇到第一个未完成的步骤，开始记录
        if stripped.startswith(("- [ ]", "- [o]", "- [O]")):
            found_active = True
            active_lines.append(line)
        # 如果已经找到了活跃步骤，且遇到了下一个步骤标记 → 结束
        elif found_active and stripped.startswith("- ["):
            break
        # 将紧跟的子描述也带上
        elif found_active:
            active_lines.append(line)

    result = "\n".join(active_lines).strip()
    return result if result else None
