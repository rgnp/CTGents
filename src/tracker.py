"""Tool-call performance tracker — 被动进化感知层的唯一数据入口。

Hooks into execute_tool() via _tracked_execute_tool() in llm.py.
Data stored alongside existing API cache stats in stats/{session_id}_tools.jsonl.

Design:
  - Append-only JSONL (cheap, crash-safe, append after each call)
  - Thread-safe buffer (SAFE parallel execution)
  - Per-session files, same dir as existing API stats
  - Zero deps beyond stdlib
  - atexit auto-flush: no code change needed in main.py
"""

from __future__ import annotations

import atexit
import json
import threading
from datetime import UTC, datetime

from .paths import STATS_DIR

_STATS_DIR = STATS_DIR
_TOOLS_SUFFIX = "_tools.jsonl"
_BUFFER_FLUSH_LIMIT = 50

_current_session_id: str = ""
_buffer: list[dict] = []
_buffer_lock = threading.Lock()


def set_session(session_id: str | None) -> None:
    """切换当前追踪会话。自动 flush 旧会话数据。"""
    global _current_session_id
    flush()
    _current_session_id = session_id or ""


def current_session() -> str:
    """当前追踪会话 id（delegate 等嵌套调用后用于恢复）。"""
    return _current_session_id


def record_tool_call(
    name: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
) -> None:
    """记录一次工具调用。线程安全。"""
    if not _current_session_id:
        return

    record = {
        "tool": name,
        "duration_ms": round(duration_ms, 2),
        "success": success,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    with _buffer_lock:
        _buffer.append(record)
        if len(_buffer) >= _BUFFER_FLUSH_LIMIT:
            _flush_locked()


def flush() -> None:
    """强制将缓冲区写入磁盘（atexit 也会调用）。"""
    with _buffer_lock:
        _flush_locked()


def _flush_locked() -> None:
    """内部：在持锁状态下写入。"""
    if not _buffer or not _current_session_id:
        return
    try:
        _STATS_DIR.mkdir(parents=True, exist_ok=True)
        path = _STATS_DIR / f"{_current_session_id}{_TOOLS_SUFFIX}"
        with open(path, "a", encoding="utf-8") as f:
            for rec in _buffer:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    _buffer.clear()


# ── 进程退出时自动 flush 残余数据 ──
atexit.register(flush)


# ═══════════════════════════════════════════════════════════════
# 聚合查询（供分析层使用）
# ═══════════════════════════════════════════════════════════════


def _read_session(session_id: str) -> list[dict]:
    """读取单次会话的工具调用记录。"""
    path = _STATS_DIR / f"{session_id}{_TOOLS_SUFFIX}"
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return records


def _discover_sessions() -> list[str]:
    """发现所有包含工具统计的会话 ID（最新在前）。"""
    if not _STATS_DIR.exists():
        return []
    sessions: list[str] = []
    for f in sorted(_STATS_DIR.iterdir(), reverse=True):
        if f.name.endswith(_TOOLS_SUFFIX):
            sid = f.name[:-len(_TOOLS_SUFFIX)]
            if sid:
                sessions.append(sid)
    return sessions


def get_session_aggregates(session_id: str) -> dict:
    """返回单次会话的工具调用聚合统计。

    Returns:
        {"session_id": str, "total_calls": int, "total_failures": int,
         "tools": {name: {"count": int, "total_ms": float, "min_ms": float,
                          "max_ms": float, "failures": int}}}
    """
    records = _read_session(session_id)
    if not records:
        return {"session_id": session_id, "total_calls": 0, "total_failures": 0, "tools": {}}

    tools: dict[str, dict] = {}
    total_failures = 0

    for r in records:
        name = r["tool"]
        dur = r["duration_ms"]
        success = r["success"]

        if name not in tools:
            tools[name] = {"count": 0, "total_ms": 0.0, "min_ms": dur, "max_ms": dur, "failures": 0}

        t = tools[name]
        t["count"] += 1
        t["total_ms"] += dur
        if dur < t["min_ms"]:
            t["min_ms"] = dur
        if dur > t["max_ms"]:
            t["max_ms"] = dur
        if not success:
            t["failures"] += 1
            total_failures += 1

    return {
        "session_id": session_id,
        "total_calls": len(records),
        "total_failures": total_failures,
        "tools": tools,
    }


def get_cross_session_baseline(recent_n: int = 10) -> dict:
    """跨会话基线：最近 N 次会话的工具性能统计。

    Returns:
        {"sessions_analyzed": int, "total_calls": int,
         "tools": {name: {"count": int, "avg_ms": float, "p50_ms": float,
                          "p95_ms": float, "failure_rate": float}}}
    """
    sessions = _discover_sessions()[:recent_n]
    if not sessions:
        return {"sessions_analyzed": 0, "total_calls": 0, "tools": {}}

    all_durations: dict[str, list[float]] = {}
    all_failures: dict[str, int] = {}
    total_calls = 0

    for sid in sessions:
        for r in _read_session(sid):
            name = r["tool"]
            all_durations.setdefault(name, []).append(r["duration_ms"])
            all_failures.setdefault(name, 0)
            if not r["success"]:
                all_failures[name] += 1
            total_calls += 1

    tools_baseline: dict[str, dict] = {}
    for name, durs in all_durations.items():
        durs_sorted = sorted(durs)
        n = len(durs_sorted)
        if n == 0:
            continue
        tools_baseline[name] = {
            "count": n,
            "avg_ms": round(sum(durs_sorted) / n, 2),
            "p50_ms": round(durs_sorted[int(n * 0.50)], 2),
            "p95_ms": round(durs_sorted[min(int(n * 0.95), n - 1)], 2),
            "failure_rate": round(all_failures.get(name, 0) / n, 3),
        }

    return {
        "sessions_analyzed": len(sessions),
        "total_calls": total_calls,
        "tools": tools_baseline,
    }


# ═══════════════════════════════════════════════════════════════
# 异常检测（分析层直接调用）
# ═══════════════════════════════════════════════════════════════

ANOMALY_SLOW_FACTOR = 3.0
ANOMALY_FAILURE_RATE = 0.30
ANOMALY_MIN_CALLS = 5
# high_volume：某工具本次调用数 ≥ 绝对地板 且 > 其它会话每会话均值 ×factor →
# 疑似撒网式重复调用（多次重叠 grep / 反复 read 同区域，烧上下文与前缀缓存）。
# 纯观测信号，不拦截——让"收不住"在实际使用里可见、可量。
ANOMALY_VOLUME_MIN = 12
ANOMALY_VOLUME_FACTOR = 3.0


def detect_anomalies(session_id: str, baseline: dict | None = None) -> list[dict]:
    """检测本次会话中的异常工具调用模式。

    Returns:
        [{"tool": name, "type": "slow"|"high_failure", "detail": str, "severity": "warn"|"crit"}, ...]
    """
    session_agg = get_session_aggregates(session_id)
    if session_agg["total_calls"] < ANOMALY_MIN_CALLS:
        return []

    if baseline is None:
        baseline = get_cross_session_baseline()

    baseline_tools = baseline.get("tools", {})
    anomalies: list[dict] = []

    for name, stats in session_agg["tools"].items():
        if stats["count"] >= ANOMALY_MIN_CALLS:
            failure_rate = stats["failures"] / stats["count"]
            if failure_rate >= ANOMALY_FAILURE_RATE:
                anomalies.append({
                    "tool": name,
                    "type": "high_failure",
                    "detail": f"{name} 本次 {stats['failures']}/{stats['count']} 次失败（{failure_rate:.0%}）",
                    "severity": "crit" if failure_rate >= 0.5 else "warn",
                })

        baseline_tool = baseline_tools.get(name)
        if baseline_tool and baseline_tool["count"] >= ANOMALY_MIN_CALLS:
            avg_ms = stats["total_ms"] / stats["count"]
            baseline_p50 = baseline_tool["p50_ms"]
            if baseline_p50 > 0 and avg_ms > baseline_p50 * ANOMALY_SLOW_FACTOR:
                anomalies.append({
                    "tool": name,
                    "type": "slow",
                    "detail": (
                        f"{name} 本次平均 {avg_ms:.0f}ms，"
                        f"基线中位数 {baseline_p50:.0f}ms（{avg_ms / baseline_p50:.1f}x）"
                    ),
                    "severity": "warn",
                })

        # high_volume：某工具本次调用数显著高于其它会话 → 疑似撒网式重复调用。
        # 基线含本会话，故按"扣掉本会话后的每会话均值"比，避免本会话自抬基线。
        if stats["count"] >= ANOMALY_VOLUME_MIN:
            n_other = max(baseline.get("sessions_analyzed", 0) - 1, 1)
            total = baseline_tool["count"] if baseline_tool else 0
            avg_other = max(total - stats["count"], 0) / n_other
            if not avg_other or stats["count"] > avg_other * ANOMALY_VOLUME_FACTOR:
                detail = f"{name} 本次 {stats['count']} 次"
                detail += (f"，其它会话约 {avg_other:.0f} 次/会话" if avg_other
                           else "（暂无基线对比）")
                detail += " — 显著偏高，疑似撒网式重复调用"
                anomalies.append({
                    "tool": name,
                    "type": "high_volume",
                    "detail": detail,
                    "severity": "warn",
                })

    return anomalies


def get_anomaly_signal_window(
    tool: str,
    anomaly_type: str,
    *,
    since: str | None = None,
    limit: int = 5,
) -> dict:
    """Measure one anomaly signal across eligible sessions.

    A session is eligible only when it contains enough exposure to the tool for
    the detector to make a meaningful decision. Sessions where the tool was not
    used are not counted as evidence that a fix worked.
    """
    try:
        since_at = datetime.fromisoformat(since) if since else None
    except ValueError:
        since_at = None
    if since_at is not None and since_at.tzinfo is None:
        since_at = since_at.replace(tzinfo=UTC)

    baseline = get_cross_session_baseline()
    candidates: list[tuple[datetime, str, int]] = []
    for session_id in _discover_sessions():
        records = _read_session(session_id)
        timestamps: list[datetime] = []
        for record in records:
            try:
                timestamp = datetime.fromisoformat(record["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            timestamps.append(timestamp)
        if not timestamps:
            continue
        ended_at = max(timestamps)
        if since_at is not None and ended_at <= since_at:
            continue

        aggregates = get_session_aggregates(session_id)
        tool_stats = aggregates["tools"].get(tool)
        if not tool_stats:
            continue
        count = tool_stats["count"]
        if anomaly_type == "high_failure":
            eligible = count >= ANOMALY_MIN_CALLS
        elif anomaly_type == "slow":
            eligible = aggregates["total_calls"] >= ANOMALY_MIN_CALLS
        elif anomaly_type == "high_volume":
            eligible = count > 0
        else:
            eligible = count > 0
        if not eligible:
            continue

        present = any(
            anomaly["tool"] == tool and anomaly["type"] == anomaly_type
            for anomaly in detect_anomalies(session_id, baseline)
        )
        candidates.append((ended_at, session_id, int(present)))

    candidates.sort(key=lambda item: item[0])
    window = candidates[-max(1, limit):]
    occurrences = sum(item[2] for item in window)
    return {
        "tool": tool,
        "anomaly_type": anomaly_type,
        "sessions_observed": len(window),
        "occurrences": occurrences,
        "occurrence_rate": round(occurrences / len(window), 3) if window else None,
        "sessions": [item[1] for item in window],
    }
