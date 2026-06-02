"""工具调用追踪器 — 记录和分析 Agent 的工具使用情况。

每条记录存一行 JSON，追加到 ~/.ctgents/tracker.jsonl。
查询接口供 /stats 和 suggest 使用。
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
MAX_RECORDS = 5000           # 文件最大行数
TRIM_EVERY_N = 100           # 每 N 次写入检查一次是否需要裁剪
MIN_STAT_SIZE = 300_000      # 文件小于此字节数不裁剪

# ── 工具分类 ──
_READ_TOOLS = {
    "read_file", "read_file_lines", "scan_project", "git_status",
    "git_diff", "git_log", "list_files", "grep_code", "rag_query",
    "read_page", "count_lines", "discover", "rag_status",
    "git_branch", "check_project", "search_web",
}
_WRITE_TOOLS = {"write_file", "edit_file_lines", "delete_file"}
_GIT_TOOLS = {"git_commit", "git_push", "git_pr"}
_META_TOOLS = {"think", "remember", "recall", "forget", "plugin_spec"}
_PLUGIN_TOOLS = {"install_plugin", "list_plugins"}
_MCP_TOOLS_PREFIX = "mcp_"


def _classify(tool_name: str) -> str:
    if tool_name in _READ_TOOLS: return "read"
    if tool_name in _WRITE_TOOLS: return "write"
    if tool_name in _GIT_TOOLS: return "git"
    if tool_name in _META_TOOLS: return "meta"
    if tool_name in _PLUGIN_TOOLS: return "plugin"
    if tool_name.startswith(_MCP_TOOLS_PREFIX): return "mcp"
    if tool_name in ("run_command", "run_python"): return "exec"
    return "other"


# ── 写入计数器（用于节流裁剪检查） ──
_write_count = 0


def record_call(tool_name: str, args: dict, success: bool,
                error: str = "", duration_ms: float = 0) -> None:
    """记录一次工具调用。"""
    global _write_count
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    args_keys = sorted(args.keys())
    args_sig_parts = [tool_name]
    for k in args_keys:
        v = args[k]
        if isinstance(v, str) and len(v) > 80:
            v = v[:80]
        args_sig_parts.append(f"{k}={v}")
    args_sig = "|".join(args_sig_parts)
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
        _write_count += 1
        if _write_count % TRIM_EVERY_N == 0:
            _trim_if_needed()
    except Exception:
        pass


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
            # 从末尾往前读 ~limit * 200 字节（一行 JSON 大概 120-180 字节）
            chunk_size = min(size, max(4096, limit * 256))
            f.seek(-chunk_size, os.SEEK_END)
            raw = f.read().decode("utf-8", errors="ignore")
            # 扔掉不完整的第一行
            if raw[0] != "{":
                nl = raw.find("\n")
                if nl != -1:
                    raw = raw[nl + 1:]
            lines = raw.strip().splitlines()
    except Exception:
        return []

    records = []
    for line in lines[-limit:]:  # 取尾部
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

    # 按分类
    cat_stats = {}
    for c, cnt in cat_counter.items():
        cat_f = sum(1 for r in records if r.get("category") == c and not r.get("success", True))
        cat_stats[c] = {"calls": cnt, "fail": cat_f}

    # 按工具
    tool_stats = {}
    for t, cnt in tool_counter.items():
        fails = tool_fail.get(t, 0)
        durs = tool_dur.get(t, [0])
        tool_stats[t] = {
            "calls": cnt, "fail": fails,
            "success_rate": round((cnt - fails) / cnt * 100, 1),
            "avg_duration_ms": round(sum(durs) / len(durs), 1),
        }

    # 重复模式
    repeated = {}
    for t, patterns in tool_args_patterns.items():
        rp = {k: v for k, v in patterns.items() if v >= 3}
        if rp:
            repeated[t] = rp

    # 最慢 top 5
    slowest = sorted(
        [(t, s["avg_duration_ms"], cnt) for t, s in tool_stats.items()
         if (cnt := s["calls"]) >= 2],
        key=lambda x: x[1], reverse=True,
    )[:5]

    # 最常用 top 10
    top_tools = sorted(tool_stats.items(), key=lambda x: x[1]["calls"], reverse=True)[:10]

    # 连续失败（最近）
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


def clear_stats() -> int:
    """清空追踪记录。返回删除的行数。"""
    global _write_count
    _write_count = 0
    if TRACKER_FILE.exists():
        cnt = sum(1 for _ in open(TRACKER_FILE, "r"))
        TRACKER_FILE.unlink()
        return cnt
    return 0


def _trim_if_needed():
    """文件太大时截断（节流调用，每 ~100 次写入检查一次）。"""
    try:
        size = TRACKER_FILE.stat().st_size
        if size < MIN_STAT_SIZE:
            return
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_RECORDS:
            with open(TRACKER_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_RECORDS // 2:])
    except Exception:
        pass
