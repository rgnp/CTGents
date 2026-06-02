"""主动建议 — 会话结束后扫描调用记录，发现问题 → 询问用户 → 触发修复闭环。

不消耗 LLM token，纯本地 JSONL 尾部扫描。
"""

from collections import Counter, defaultdict

from .tools.tracker import get_recent

# ── 阈值 ──
CONSECUTIVE_FAIL_LIMIT = 2
REPEATED_CALL_LIMIT = 3
SLOW_TOOL_LIMIT_MS = 2000
SLOW_ASK_LIMIT_MS = 5000
IGNORE_FOR_REPEAT = frozenset({"think", "remember", "recall", "forget"})


def check() -> tuple[str | None, str | None]:
    """返回 (显示文本, 修复提示)。

    两者要么都为 None（没问题闭嘴），要么都有。
    修复提示直接作为 prompt 发给 LLM 即可执行修复。
    """
    recent = get_recent(20)
    if not recent:
        return None, None

    issues: list[str] = []      # 人类可读的描述
    actions: list[str] = []      # 发给 LLM 的行动指令

    fail_streak = 0
    fail_tools: list[str] = []
    sig_counter: Counter = Counter()
    tool_durations: defaultdict[str, list] = defaultdict(list)
    streak_broken = False

    for r in reversed(recent):
        tool = r.get("tool", "?")
        ok = r.get("success", True)
        if not streak_broken:
            if not ok:
                fail_streak += 1
                err = r.get("error", "")[:40]
                fail_tools.append(f"{tool}({err})")
            else:
                streak_broken = True
        if tool not in IGNORE_FOR_REPEAT:
            sig = r.get("args_sig") or ",".join(r.get("args_keys", []))
            sig_counter[(tool, sig)] += 1
        dur = r.get("duration_ms", 0)
        if dur > 0:
            tool_durations[tool].append(dur)

    # ── 连续失败 ──
    if fail_streak >= CONSECUTIVE_FAIL_LIMIT:
        details = " → ".join(fail_tools)
        issues.append(f"⚠️ 连续 {fail_streak} 次调用失败：{details}")
        # 附带历史反思记录
        from .tools.reflect import get_failures
        hist = get_failures(fail_tools[0].split("(")[0])
        hist_hint = f"\n  历史失败记录：\n{hist}" if hist else ""
        actions.append(
            f"- 工具连续失败 {fail_streak} 次：{fail_tools[0]}。"
            f"{hist_hint}"
            f"\n  请分析错误原因，检查参数、网络或依赖，然后修复。"
        )

    # ── 重复调用 ──
    for (tool, keys), count in sig_counter.most_common():
        if count >= REPEATED_CALL_LIMIT:
            suffix = "，考虑缓存结果，是否需要封装？" if count >= 5 else ""
            issues.append(f"🔄 {tool}({keys}) 调了 {count} 次{suffix}")
            if count >= 5:
                actions.append(
                    f"- {tool}({keys}) 高频调用 {count} 次，"
                    f"考虑添加缓存或封装成批量工具。"
                )

    # ── 慢工具 ──
    for tool, durs in tool_durations.items():
        if len(durs) >= 2:
            avg = sum(durs) / len(durs)
            if avg >= SLOW_TOOL_LIMIT_MS:
                suffix = "，要优化吗？" if avg >= SLOW_ASK_LIMIT_MS else ""
                issues.append(f"🐢 {tool} 均耗时 {avg:.0f}ms（{len(durs)} 次）{suffix}")
                if avg >= SLOW_ASK_LIMIT_MS:
                    actions.append(
                        f"- {tool} 均耗时 {avg:.0f}ms（{len(durs)} 次），"
                        f"属于慢工具，请分析是否可以优化。"
                    )

    if not issues:
        return None, None

    hint = "\n".join(issues)
    repair = (
        "根据自检发现以下问题，请逐项分析并修复：\n"
        + "\n".join(actions)
        + "\n\n修复步骤："
        + "\n1. 读取相关源码确认问题根因"
        + "\n2. 修改代码或配置"
        + "\n3. 运行测试验证"
        + "\n4. 提交修复"
    )
    return hint, repair
