"""主动建议 — 会话结束后扫描调用记录，发现值得说的就开口。

O(1) 内存、O(n) 时间（n=20），纯本地扫描，不消耗 LLM token。
"""

from collections import Counter, defaultdict

from .tools.tracker import get_recent

# ── 阈值 ──
CONSECUTIVE_FAIL_LIMIT = 2
REPEATED_CALL_LIMIT = 3
SLOW_TOOL_LIMIT_MS = 2000
SLOW_ASK_LIMIT_MS = 5000
IGNORE_FOR_REPEAT = frozenset({"think", "remember", "recall", "forget"})


def check() -> str | None:
    """扫描最近 20 条记录，有问题返回建议，没问题返回 None。"""
    recent = get_recent(20)
    if not recent:
        return None

    suggestions: list[str] = []

    # ── 单次遍历：收集失败连续性 + 工具计数 + 耗时 ──
    fail_streak = 0
    fail_tools: list[str] = []
    sig_counter: Counter = Counter()
    tool_durations: defaultdict[str, list] = defaultdict(list)
    streak_broken = False

    for r in reversed(recent):
        tool = r.get("tool", "?")
        ok = r.get("success", True)

        # 连续失败（从最新往前）
        if not streak_broken:
            if not ok:
                fail_streak += 1
                err = r.get("error", "")[:40]
                fail_tools.append(f"{tool}({err})")
            else:
                streak_broken = True

        # 重复调用签名
        if tool not in IGNORE_FOR_REPEAT:
            keys = ",".join(r.get("args_keys", []))
            sig_counter[(tool, keys)] += 1

        # 耗时
        dur = r.get("duration_ms", 0)
        if dur > 0:
            tool_durations[tool].append(dur)

    # ── 生成建议 ──

    if fail_streak >= CONSECUTIVE_FAIL_LIMIT:
        suggestions.append(
            f"⚠️ 连续 {fail_streak} 次调用失败：{' → '.join(fail_tools)}"
        )

    for (tool, keys), count in sig_counter.most_common():
        if count >= REPEATED_CALL_LIMIT:
            suffix = "，考虑缓存结果，是否需要封装？" if count >= 5 else ""
            suggestions.append(f"🔄 {tool}({keys}) 调了 {count} 次{suffix}")

    for tool, durs in tool_durations.items():
        if len(durs) >= 2:
            avg = sum(durs) / len(durs)
            if avg >= SLOW_TOOL_LIMIT_MS:
                suffix = "，要优化吗？" if avg >= SLOW_ASK_LIMIT_MS else ""
                suggestions.append(
                    f"🐢 {tool} 均耗时 {avg:.0f}ms（{len(durs)} 次）{suffix}"
                )

    return "\n".join(suggestions) if suggestions else None
