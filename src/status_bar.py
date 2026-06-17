"""命令行状态显示 — 两件互补的东西，让你边用边对项目有数：

1. 底部状态条（bottom_toolbar，常驻）：当前状态——上下文充满度（压缩崖预警）/
   缓存命中率 / 当前任务 / 上一轮 Δmiss。
2. 每轮收尾小结（footer，打印在对话后）：刚才这轮花了啥——耗时 / 输出 token /
   请求数 / 本轮 miss（突刺告警）。留在滚动历史里可回看。

per-turn 增量（耗时除外）由 note_turn_start()/note_turn_end() 在一处算一次：
footer 立即用、状态条 Δmiss 复用同一份，两边数对得上、不各自快照。

设计约束（与 [[ctgents-context-cache]] 一脉）：
- 纯只读，在 agent/缓存循环之外：绝不触发 LLM、不碰前缀缓存、不写状态文件。
- 读的是内存里当前会话的累计统计（get_cache_stats("")），不读盘。
- 任何异常都吞掉：状态显示永不拖垮 REPL / 一轮对话。
- 底部条每轮 refresh() 算一次缓存，bottom_toolbar 回调只取缓存文本——
  prompt_toolkit 每次按键都回调，实时算 count_messages_tokens 会让大上下文打字卡。
"""

from __future__ import annotations

import html as _html
import time

# 朝 65% 自动压缩崖逼近时变色——压缩是唯一的前缀缓存破坏者，提前预警比静默触发好
_WARN_PCT = 55.0
_CRIT_PCT = 62.0

# 突刺判定：本轮 miss 超过最近若干轮中位数的倍数，且绝对量够大才算
_SPIKE_FACTOR = 2.0
_SPIKE_FLOOR = 2000
_RECENT_KEEP = 10

_state: dict = {
    "text": None,       # 缓存的 toolbar 文本（HTML 对象 / None）
    "last_turn": None,   # 上一轮增量 {elapsed,out,req,miss,spike}，footer 与状态条共用
}
_turn: dict = {"t0": 0.0, "set": False}  # 轮起点（仅耗时；增量走 llm 单轮累加器）
_recent_miss: list[int] = []  # 最近若干轮的 per-turn miss，用于突刺判定


# ── 底部状态条 ──

def refresh(ctx, session_id: str) -> None:
    """每轮在 prompt 前调用：重算状态条文本并缓存。绝不抛。"""
    try:
        _state["text"] = _build(ctx, session_id or "")
    except Exception:
        _state["text"] = None


def text():
    """bottom_toolbar 回调：返回缓存的状态条文本（HTML / None）。"""
    return _state["text"]


# ── 每轮收尾小结 ──

def note_turn_start() -> None:
    """一轮 agent 驱动开始前调用：复位 llm 单轮累加器、记起点时间。绝不抛。"""
    try:
        from .llm import reset_turn_accum
        reset_turn_accum()
        _turn["t0"] = time.perf_counter()
        _turn["set"] = True
    except Exception:
        _turn["set"] = False


def note_turn_end() -> str | None:
    """一轮结束后调用：读单轮累加器增量，存进 last_turn（供状态条复用），返回 footer。

    增量走 llm._turn_accum（每个真实请求就地累加），不受会话切换重置统计指针影响。
    返回 None=没有有效起点 / 出错。绝不抛。
    """
    try:
        if not _turn.get("set"):
            return None
        from .llm import get_turn_accum
        a = get_turn_accum()
        dreq, dout, dmiss = a["requests"], a["completion_tokens"], a["miss"]
        elapsed = max(time.perf_counter() - _turn["t0"], 0.0)
        _turn["set"] = False
        spike = _is_spike(dmiss, _recent_miss)
        _recent_miss.append(dmiss)
        del _recent_miss[:-_RECENT_KEEP]
        _state["last_turn"] = {"elapsed": elapsed, "out": dout, "req": dreq,
                               "miss": dmiss, "spike": spike}
        return _footer(_state["last_turn"])
    except Exception:
        return None


def reset() -> None:
    """会话切换 / 清空时复位 per-turn 基线（避免跨会话误算增量）。"""
    _turn["set"] = False
    _recent_miss.clear()
    _state["last_turn"] = None


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


def _footer(lt: dict) -> str:
    """组装每轮收尾小结一行。"""
    parts = [f"{lt['elapsed']:.1f}s", f"输出 {lt['out']:,} tok"]
    if lt["req"]:
        parts.append(f"{lt['req']} 请求")
    if lt["miss"] > 0:
        parts.append(f"miss {_fmt_k(lt['miss'])}" + (" 突刺" if lt["spike"] else ""))
    return "  本轮 " + " · ".join(parts)


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

    # ── 缓存命中率（头号指标）──
    from .llm import get_cache_stats
    t = (get_cache_stats(session_id) or {}).get("total", {})
    prompt_t = t.get("prompt_tokens", 0)
    hit = t.get("cache_hit_tokens", 0)
    if prompt_t > 0:
        segs.append(f"cache {hit / prompt_t * 100:.0f}%")

    # ── 上一轮 Δmiss（突刺告警）──复用 note_turn_end 算好的 last_turn ──
    lt = _state.get("last_turn")
    if lt and lt.get("miss", 0) > 0:
        dlabel = f"Δmiss {_fmt_k(lt['miss'])}"
        segs.append(f"<ansired>{dlabel} 突刺</ansired>" if lt.get("spike") else dlabel)

    # ── 后台作业（运行中作业数）──
    try:
        from .tools.exec import running_job_count
        njobs = running_job_count()
        if njobs:
            segs.append(f"⏳{njobs} 后台")
    except Exception:
        pass

    # ── 当前任务 ──
    from .tasks import has_unfinished, read_current
    if has_unfinished():
        title = _task_title(read_current())
        if title:
            segs.append(f"▶ {_html.escape(title)}")

    return HTML(" │ ".join(segs)) if segs else None
