"""主动建议 — 会话结束后扫描调用记录，发现值得说的就开口。

不消耗 LLM token，纯本地 JSONL 扫描。
"""

from .tools.tracker import get_recent

# ── 阈值 ──
CONSECUTIVE_FAIL_LIMIT = 2    # 连续失败 >= 这个数就说
REPEATED_CALL_LIMIT = 3       # 同工具同参数 key >= 这个数就说
SLOW_TOOL_LIMIT_MS = 2000     # 均耗时超过这个就说


def check() -> str | None:
    """扫描最近记录，有值得说的返回建议文本，没有返回 None。"""
    recent = get_recent(20)
    if not recent:
        return None

    suggestions: list[str] = []

    # ── 1. 检查连续失败 ──
    fail_streak = 0
    fail_tools = []
    # 从最新往前数连续失败
    for r in reversed(recent):
        if not r.get("success", True):
            fail_streak += 1
            tool = r.get("tool", "?")
            err = r.get("error", "")[:40]
            fail_tools.append(f"{tool}({err})")
        else:
            break
    if fail_streak >= CONSECUTIVE_FAIL_LIMIT:
        suggestions.append(
            f"⚠️ 连续 {fail_streak} 次调用失败：{' → '.join(fail_tools)}"
        )

    # ── 2. 检查重复调用 ──
    from collections import Counter
    sig_counter: Counter = Counter()
    for r in recent:
        tool = r.get("tool", "?")
        keys = ",".join(r.get("args_keys", []))
        sig_counter[(tool, keys)] += 1

    for (tool, keys), count in sig_counter.most_common():
        if count >= REPEATED_CALL_LIMIT and tool not in ("think", "remember", "recall"):
            suggestions.append(
                f"🔄 {tool}({keys}) 调了 {count} 次"
                + ("，考虑缓存结果，是否需要封装？" if count >= 5 else "")
            )

    # ── 3. 检查慢工具 ──
    from collections import defaultdict
    tool_durations: defaultdict[str, list] = defaultdict(list)
    for r in recent:
        dur = r.get("duration_ms", 0)
        if dur > 0:
            tool_durations[r.get("tool", "?")].append(dur)
    for tool, durs in tool_durations.items():
        if len(durs) >= 2:
            avg = sum(durs) / len(durs)
            if avg >= SLOW_TOOL_LIMIT_MS:
                suggestions.append(
                    f"🐢 {tool} 均耗时 {avg:.0f}ms（{len(durs)} 次）"
                    + ("，要优化吗？" if avg >= 5000 else "")
                )

    if not suggestions:
        return None

    return "\n".join(suggestions)
