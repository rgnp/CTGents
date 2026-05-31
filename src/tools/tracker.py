"""工具调用追踪器 — 记录和分析 Agent 的工具使用情况。

每条记录存一行 JSON，追加到 ~/.ctgents/tracker.jsonl。
提供查询接口供 /stats 命令和自省使用。
"""

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# ── 配置 ──
TRACKER_DIR = Path(os.path.expanduser("~")) / ".ctgents"
TRACKER_FILE = TRACKER_DIR / "tracker.jsonl"
MAX_RECORDS = 5000  # 文件最大行数，超过时截断后半

# ── 工具分类（供统计分组用） ──
_READ_TOOLS = {
    "read_file", "read_file_lines", "scan_project", "git_status",
    "git_diff", "git_log", "list_files", "grep_code", "rag_query",
    "read_page", "count_lines", "discover", "rag_status",
    "git_branch", "check_project", "search_web",
}
_WRITE_TOOLS = {
    "write_file", "edit_file_lines", "delete_file",
}
_GIT_TOOLS = {
    "git_commit", "git_push", "git_pr",
}
_META_TOOLS = {
    "think", "remember", "recall", "forget", "plugin_spec",
}
_PLUGIN_TOOLS = {"install_plugin", "list_plugins"}
_MCP_TOOLS_PREFIX = "mcp_"


def _classify(tool_name: str) -> str:
    """将工具归入大类。"""
    if tool_name in _READ_TOOLS:
        return "read"
    if tool_name in _WRITE_TOOLS:
        return "write"
    if tool_name in _GIT_TOOLS:
        return "git"
    if tool_name in _META_TOOLS:
        return "meta"
    if tool_name in _PLUGIN_TOOLS:
        return "plugin"
    if tool_name.startswith(_MCP_TOOLS_PREFIX):
        return "mcp"
    if tool_name in ("run_command", "run_python"):
        return "exec"
    return "other"


def ensure_dir():
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)


def record_call(tool_name: str, args: dict, success: bool,
                error: str = "", duration_ms: float = 0) -> None:
    """记录一次工具调用。"""
    ensure_dir()
    # 只记录参数key+类型，不记录具体值（保护隐私 + 减少体积）
    args_keys = sorted(args.keys())
    args_sig = {k: type(v).__name__ for k, v in args.items()}

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tool": tool_name,
        "category": _classify(tool_name),
        "args_keys": args_keys,
        "args_sig": args_sig,
        "success": success,
        "error": error[:200] if error else "",
        "duration_ms": round(duration_ms, 1),
    }
    try:
        with open(TRACKER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _trim_if_needed()
    except Exception:
        pass  # 记录失败不影响主流程


# ── 查询接口 ──

def _load_records() -> list[dict]:
    """从文件加载所有记录。"""
    if not TRACKER_FILE.exists():
        return []
    records = []
    try:
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return records


def get_stats() -> dict:
    """返回汇总统计。"""
    records = _load_records()
    if not records:
        return {"total": 0, "message": "暂无记录"}

    total = len(records)
    success_count = sum(1 for r in records if r.get("success", True))
    fail_count = total - success_count

    # 按工具统计
    tool_counter: Counter = Counter()
    tool_success: Counter = Counter()
    tool_fail: Counter = Counter()
    tool_duration: defaultdict[str, list] = defaultdict(list)

    # 按分类统计
    cat_counter: Counter = Counter()
    cat_fail: Counter = Counter()

    # 参数模式检测
    tool_args_patterns: defaultdict[str, Counter] = defaultdict(Counter)

    for r in records:
        t = r.get("tool", "?")
        cat = r.get("category", "other")
        ok = r.get("success", True)
        tool_counter[t] += 1
        cat_counter[cat] += 1
        if ok:
            tool_success[t] += 1
        else:
            tool_fail[t] += 1
            cat_fail[cat] += 1
        dur = r.get("duration_ms", 0)
        if dur > 0:
            tool_duration[t].append(dur)

        # 参数模式（只记key组合）
        keys = tuple(r.get("args_keys", []))
        tool_args_patterns[t][keys] += 1

    # 组装结果
    tool_stats = {}
    for t in tool_counter:
        cnt = tool_counter[t]
        ok = tool_success.get(t, 0)
        fails = tool_fail.get(t, 0)
        avg_dur = round(sum(tool_duration.get(t, [0])) / max(len(tool_duration.get(t, [])), 1), 1)
        tool_stats[t] = {
            "calls": cnt,
            "success": ok,
            "fail": fails,
            "success_rate": round(ok / cnt * 100, 1) if cnt else 0,
            "avg_duration_ms": avg_dur,
        }

    # 按分类统计
    cat_stats = {}
    for c in cat_counter:
        cnt = cat_counter[c]
        fails = cat_fail.get(c, 0)
        cat_stats[c] = {"calls": cnt, "fail": fails}

    # 重复模式（相同参数key组合调用 >= 3 次）
    repeated = {}
    for t, patterns in tool_args_patterns.items():
        repeated_tools = {k: v for k, v in patterns.items() if v >= 3}
        if repeated_tools:
            repeated[t] = repeated_tools

    # 最慢工具 top 5
    slowest = sorted(
        [(t, s["avg_duration_ms"], s["calls"]) for t, s in tool_stats.items() if s["calls"] >= 2],
        key=lambda x: x[1], reverse=True,
    )[:5]

    # 最常用工具 top 10
    top_tools = sorted(tool_stats.items(), key=lambda x: x[1]["calls"], reverse=True)[:10]

    # 连续失败（最近记录中连续失败的）
    recent_fails = []
    for r in reversed(records[-30:]):
        if not r.get("success", True):
            recent_fails.append(f"{r.get('tool','?')}: {r.get('error','')[:60]}")
        else:
            break  # 遇到成功的就停

    return {
        "total": total,
        "success": success_count,
        "fail": fail_count,
        "success_rate": round(success_count / total * 100, 1) if total else 0,
        "by_tool": tool_stats,
        "by_category": cat_stats,
        "top_tools": [{"name": t, **s} for t, s in top_tools],
        "slowest_tools": [{"name": t, "avg_duration_ms": d, "calls": c} for t, d, c in slowest],
        "repeated_patterns": repeated,
        "consecutive_fails": recent_fails,
        "session_count": len(set(r.get("session_id", "") for r in records if r.get("session_id"))),
    }


def get_recent(limit: int = 20) -> list[dict]:
    """返回最近 N 条记录。"""
    records = _load_records()
    return records[-limit:]


def clear_stats() -> int:
    """清空追踪记录。返回删除的行数。"""
    if TRACKER_FILE.exists():
        cnt = sum(1 for _ in open(TRACKER_FILE, "r"))
        TRACKER_FILE.unlink()
        return cnt
    return 0


def _trim_if_needed():
    """文件太大时截断，保留后半。"""
    try:
        if TRACKER_FILE.stat().st_size < 500_000:  # 小于 500KB 不处理
            return
        with open(TRACKER_FILE, "r") as f:
            lines = f.readlines()
        if len(lines) > MAX_RECORDS:
            with open(TRACKER_FILE, "w") as f:
                f.writelines(lines[-MAX_RECORDS // 2:])
    except Exception:
        pass
