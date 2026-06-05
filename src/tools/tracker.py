"""工具调用追踪器 — 记录和分析 Agent 的工具使用情况。

查询接口供 /stats 和 suggest 使用。
注意：record_call() 当前未接入 execute_tool()，写端待重新启用。
"""

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

# ── 配置 ──
TRACKER_DIR = Path(os.path.expanduser("~")) / ".ctgents"
TRACKER_FILE = TRACKER_DIR / "tracker.jsonl"

# ── 工具分类 ──
_READ_TOOLS = {
    "read_file", "read_file_lines", "scan_project", "git_status",
    "git_diff", "git_log", "list_files", "grep_code", "rag_query",
    "read_page", "count_lines", "rag_status",
    "git_branch", "check_project", "search_web",
}
_WRITE_TOOLS = {"write_file", "edit_file_lines", "delete_file"}
_GIT_TOOLS = {"git_commit", "git_push", "git_pr"}
_META_TOOLS = {"think", "remember", "recall", "forget"}


def _classify(tool_name: str) -> str:
    if tool_name in _READ_TOOLS: return "read"
    if tool_name in _WRITE_TOOLS: return "write"
    if tool_name in _GIT_TOOLS: return "git"
    if tool_name in _META_TOOLS: return "meta"
    if tool_name in ("run_command", "run_python"): return "exec"
    return "other"


# ── 查询接口 ──

def _read_tail(limit: int) -> list[dict]:
    """只读文件尾部 N 行，避免全量加载。"""
    if not TRACKER_FILE.exists():
        return []
    try:
        with open(TRACKER_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            chunk_size = min(size, max(4096, limit * 256))
            f.seek(-chunk_size, os.SEEK_END)
            raw = f.read().decode("utf-8", errors="ignore")
            if raw[0] != "{":
                nl = raw.find("\n")
                if nl != -1:
                    raw = raw[nl + 1:]
            lines = raw.strip().splitlines()
    except Exception:
        return []

    records = []
    for line in lines[-limit:]:
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _load_all() -> list[dict]:
    """全量加载（仅 /stats 用）。"""
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


def get_recent(limit: int = 20) -> list[dict]:
    """返回最近 N 条调用记录（只读尾部，O(1) 内存）。"""
    return _read_tail(limit)


def get_stats() -> dict:
    """返回汇总统计（全量统计，低频调用）。"""
    records = _load_all()
    if not records:
        return {"total": 0, "message": "暂无记录"}

    total = len(records)
    ok_count = sum(1 for r in records if r.get("success", True))
    fail_count = total - ok_count

    tool_counter: Counter = Counter()
    tool_fail: Counter = Counter()
    tool_dur: defaultdict[str, list] = defaultdict(list)
    cat_counter: Counter = Counter()
    tool_args_patterns: defaultdict[str, Counter] = defaultdict(Counter)

    for r in records:
        t = r.get("tool", "?")
        cat = r.get("category", "other")
        ok = r.get("success", True)
        tool_counter[t] += 1
        cat_counter[cat] += 1
        if not ok:
            tool_fail[t] += 1
        dur = r.get("duration_ms", 0)
        if isinstance(dur, (int, float)) and dur > 0:
            tool_dur[t].append(float(dur))
        keys = tuple(r.get("args_keys", []))
        tool_args_patterns[t][keys] += 1

    cat_stats = {}
    for c, cnt in cat_counter.items():
        cat_f = sum(1 for r in records if r.get("category") == c and not r.get("success", True))
        cat_stats[c] = {"calls": cnt, "fail": cat_f}

    tool_stats = {}
    for t, cnt in tool_counter.items():
        fails = tool_fail.get(t, 0)
        durs = tool_dur.get(t, [0])
        tool_stats[t] = {
            "calls": cnt, "fail": fails,
            "success_rate": round((cnt - fails) / cnt * 100, 1),
            "avg_duration_ms": round(sum(durs) / len(durs), 1),
        }

    repeated = {}
    for t, patterns in tool_args_patterns.items():
        rp = {k: v for k, v in patterns.items() if v >= 3}
        if rp:
            repeated[t] = rp

    slowest = sorted(
        [(t, s["avg_duration_ms"], cnt) for t, s in tool_stats.items()
         if (cnt := s["calls"]) >= 2],
        key=lambda x: x[1], reverse=True,
    )[:5]

    top_tools = sorted(tool_stats.items(), key=lambda x: x[1]["calls"], reverse=True)[:10]

    recent_fails = []
    for r in reversed(records[-30:]):
        if not r.get("success", True):
            recent_fails.append(f"{r.get('tool','?')}: {r.get('error','')[:60]}")
        else:
            break

    return {
        "total": total, "success": ok_count, "fail": fail_count,
        "success_rate": round(ok_count / total * 100, 1) if total else 0,
        "by_tool": tool_stats, "by_category": cat_stats,
        "top_tools": [{"name": t, **s} for t, s in top_tools],
        "slowest_tools": [{"name": n, "avg_duration_ms": d, "calls": c} for n, d, c in slowest],
        "repeated_patterns": repeated,
        "consecutive_fails": recent_fails,
    }
