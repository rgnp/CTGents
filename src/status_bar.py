"""命令行底部状态条 — 把该盯的几个数常驻可见，不必每轮敲 /context。

显示四段：上下文充满度（压缩崖预警）/ 缓存命中率（头号指标）/
当前任务 / 上一轮 miss（突刺告警）。

设计约束（与 [[ctgents-context-cache]] 一脉）：
- 纯只读渲染，在 agent/缓存循环之外：绝不触发 LLM、不碰前缀缓存、不写任何状态文件。
- 每轮刷新一次（refresh()），bottom_toolbar 回调只返回缓存文本（text()）。
  prompt_toolkit 每次按键都会回调 toolbar，而 count_messages_tokens 是 O(消息数)，
  实时算会让大上下文下打字卡顿——所以算一次、缓存、回调直接取。
- 任何异常都吞掉：状态条永不拖垮 REPL 输入。
"""

from __future__ import annotations

import html as _html

# 朝 65% 自动压缩崖逼近时变色——压缩是唯一的前缀缓存破坏者，提前预警比静默触发好
_WARN_PCT = 55.0
_CRIT_PCT = 62.0

# 突刺判定：本轮 miss 超过最近若干轮中位数的倍数，且绝对量够大才算
_SPIKE_FACTOR = 2.0
_SPIKE_FLOOR = 2000
_RECENT_KEEP = 10

_state: dict = {
    "text": None,            # 缓存的 toolbar 文本（HTML 对象 / None）
    "last_cum_miss": -1,     # 上次刷新时的累计 miss（-1=未初始化，首轮不报 Δ）
    "recent_turn_miss": [],  # 最近若干轮的 per-turn miss，用于突刺判定
}


def refresh(ctx, session_id: str) -> None:
    """每轮在 prompt 前调用：重算状态文本并缓存。绝不抛。"""
    try:
        _state["text"] = _build(ctx, session_id or "")
    except Exception:
        _state["text"] = None


def text():
    """bottom_toolbar 回调：返回缓存的状态文本（HTML / None）。"""
    return _state["text"]


def reset() -> None:
    """会话切换 / 清空时复位 per-turn 累计基线（避免跨会话误算 Δmiss）。"""
    _state["last_cum_miss"] = -1
    _state["recent_turn_miss"] = []


# ── 内部 ──

def _fmt_k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(int(n))


def _is_spike(delta: int, recent: list[int]) -> bool:
    if delta < _SPIKE_FLOOR or len(recent) < 3:
        return False
    import statistics
    med = statistics.median(recent)
    return med > 0 and delta > med * _SPIKE_FACTOR


def _task_title(current_md: str) -> str:
    for line in current_md.splitlines():
        s = line.lstrip("#").strip()
        if s:
            return s[:24]
    return ""


def _build(ctx, session_id: str):
    from prompt_toolkit.formatted_text import HTML

    segs: list[str] = []

    # ── 上下文充满度（压缩崖预警）──
    from .config import MAX_CONTEXT_TOKENS
    from .tools.tokens import count_messages_tokens
    used = count_messages_tokens(ctx.all)
    pct = used / MAX_CONTEXT_TOKENS * 100 if MAX_CONTEXT_TOKENS else 0
    label = f"ctx {pct:.0f}%"
    if pct >= _CRIT_PCT:
        segs.append(f"<ansired>{label} 压缩临近</ansired>")
    elif pct >= _WARN_PCT:
        segs.append(f"<ansiyellow>{label}</ansiyellow>")
    else:
        segs.append(label)

    # ── 缓存命中率（头号指标）+ 上一轮 miss（突刺告警）──
    from .llm import get_cache_stats
    t = (get_cache_stats(session_id) or {}).get("total", {})
    prompt_t = t.get("prompt_tokens", 0)
    hit = t.get("cache_hit_tokens", 0)
    miss = prompt_t - hit
    if prompt_t > 0:
        segs.append(f"cache {hit / prompt_t * 100:.0f}%")

    last = _state["last_cum_miss"]
    if last >= 0 and miss >= last:
        delta = miss - last
        recent = _state["recent_turn_miss"]
        spike = _is_spike(delta, recent)
        recent.append(delta)
        del recent[:-_RECENT_KEEP]
        if delta:
            dlabel = f"Δmiss {_fmt_k(delta)}"
            segs.append(f"<ansired>{dlabel} 突刺</ansired>" if spike else dlabel)
    _state["last_cum_miss"] = miss

    # ── 当前任务 ──
    from .tasks import has_unfinished, read_current
    if has_unfinished():
        title = _task_title(read_current())
        if title:
            segs.append(f"▶ {_html.escape(title)}")

    return HTML(" │ ".join(segs)) if segs else None
