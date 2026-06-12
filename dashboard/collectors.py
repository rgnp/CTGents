"""状态采集 — 只读，复用 src 读函数 / 直接读盘 artifact。

每个采集器独立 try/except（gather_state 的 guard）：一个数据源坏掉不拖垮整个面板。
缓存命中率刻意直接读 stats/{sid}.json（形状镜像 llm.py _CACHE_STATS），
不 import llm.py——那会把 API 客户端栈拉进监控进程，违背解耦。
其余非平凡解析（frontmatter / gap 缓存 / 门审计）复用 src，避免重写产生漂移。
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATS_DIR = PROJECT_ROOT / "stats"

_EMPTY_CACHE = {
    "requests": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_hit_tokens": 0,
    "cache_miss_tokens": 0,
}


def _git(args: list[str]) -> str:
    r = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=5, cwd=PROJECT_ROOT,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def collect_git() -> dict:
    """进化时间线：最近提交 + 当前分支。"""
    log = _git(["log", "--format=%h\x1f%cI\x1f%s", "-20"])
    commits = []
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return {"branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]), "commits": commits}


def collect_trust() -> dict:
    """可信信号：门通行证审计（绕门提交会在此暴露）。"""
    from src.gate_audit import head_gate_notice
    notice = head_gate_notice()
    return {"ok": not notice, "gate": notice or "PASS（HEAD 有通行证，审计静默）"}


_DONE_SECTIONS = ("已交付", "已完成", "归档")


def _parse_ambitions(text: str) -> list[dict]:
    """把 ambitions.md 解析成 `## section` + 其下 `- bullet` 列表。"""
    sections: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            cur = {"section": s[3:].strip(), "items": []}
            sections.append(cur)
        elif cur is not None and s.startswith("- "):
            cur["items"].append(s[2:].strip().replace("**", ""))
    return sections


def _to_int(v) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def collect_memory() -> dict:
    """记忆 & 野心：lessons（含指纹/遭遇次数，按高频失败排）+ ambitions。"""
    from src.tasks import read_ambitions
    from src.tools.memory import _dir, _split_frontmatter

    lessons = []
    for f in sorted(_dir().glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
        lessons.append({
            "name": meta.get("name", f.stem),
            "type": meta.get("type", ""),
            "fingerprint": meta.get("fingerprint", ""),
            "times": _to_int(meta.get("times_encountered", "")),
            "desc": meta.get("description", "")[:80],
        })
    lessons.sort(key=lambda x: x["times"], reverse=True)
    ambitions = read_ambitions()
    sections = _parse_ambitions(ambitions)
    active = [s for s in sections if s["section"] not in _DONE_SECTIONS and s["items"]]
    delivered = sum(len(s["items"]) for s in sections if s["section"] in _DONE_SECTIONS)
    return {
        "count": len(lessons),
        "lessons": lessons,
        "recurring": [x for x in lessons if x["times"] >= 2][:8],
        "ambition_sections": sections,
        "ambition_active": active,
        "ambition_delivered": delivered,
    }


def collect_anomalies(recent_n: int = 6) -> dict:
    """自反思：最近会话检测到的异常工具行为（stats/*_reflection.json）。"""
    if not STATS_DIR.exists():
        return {"recent": []}
    files = sorted(
        STATS_DIR.glob("*_reflection.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:recent_n]
    recent = []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        recent.append({
            "session": d.get("session_id", p.stem.replace("_reflection", "")),
            "timestamp": d.get("timestamp", ""),
            "anomalies": d.get("anomalies", []),
        })
    return {"recent": recent}


def collect_tools(top_n: int = 6, min_calls: int = 3) -> dict:
    """工具性能基线：最慢 / 失败率最高（tracker 跨会话聚合）。

    min_calls 过滤单次调用噪声——n=1 的 30s 不是有意义的"最慢"信号。
    """
    from src.tracker import get_cross_session_baseline
    base = get_cross_session_baseline()
    items = [
        {"tool": k, **v}
        for k, v in base.get("tools", {}).items()
        if v.get("count", 0) >= min_calls
    ]
    slowest = sorted(items, key=lambda x: x.get("p50_ms", 0), reverse=True)[:top_n]
    failing = sorted(
        (x for x in items if x.get("failure_rate", 0) > 0),
        key=lambda x: x.get("failure_rate", 0), reverse=True,
    )[:top_n]
    return {
        "sessions_analyzed": base.get("sessions_analyzed", 0),
        "total_calls": base.get("total_calls", 0),
        "slowest": slowest,
        "failing": failing,
    }


def collect_tasks() -> dict:
    """任务态：当前任务 + 进度行 + 完成标记。"""
    from src.tasks import (
        get_task_progress_line,
        has_unfinished,
        is_all_done,
        read_current,
    )
    return {
        "current": read_current(),
        "progress": get_task_progress_line(),
        "unfinished": has_unfinished(),
        "all_done": is_all_done(),
    }


def collect_gaps() -> dict:
    """改进方向：复用 gaps 的 gap 缓存解析（_load_gap_cache，不触发 5s 冷算）。"""
    from src.gaps import _git_tree_hash, _load_gap_cache
    report = _load_gap_cache(_git_tree_hash())
    if report is None:
        return {"available": False, "gaps": []}
    return {
        "available": True,
        "sources_scanned": report.sources_scanned,
        "sources_failed": report.sources_failed,
        "gaps": [
            {
                "source": g.source,
                "type": g.gap_type,
                "severity": g.severity,
                "detail": g.detail,
                "suggestion": g.suggestion,
            }
            for g in report.gaps
        ],
    }


def _read_cache_file(p: Path) -> dict:
    data = json.loads(p.read_text(encoding="utf-8"))
    pro = data.get("pro", {}) if isinstance(data, dict) else {}
    return {k: pro.get(k, 0) for k in _EMPTY_CACHE}


def collect_performance(recent_n: int = 12) -> dict:
    """性能：DeepSeek 前缀缓存命中率（#1 目标）——直接读 stats/{sid}.json。"""
    if not STATS_DIR.exists():
        return {"overall_hit_rate": 0.0, "total_requests": 0, "sessions": []}
    files = sorted(
        (p for p in STATS_DIR.glob("*.json") if not p.stem.endswith("_reflection")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:recent_n]

    sessions = []
    agg = dict(_EMPTY_CACHE)
    for p in files:
        try:
            s = _read_cache_file(p)
        except Exception:
            continue
        hit, miss = s["cache_hit_tokens"], s["cache_miss_tokens"]
        rate = hit / (hit + miss) if (hit + miss) else 0.0
        sessions.append({
            "session": p.stem,
            "requests": s["requests"],
            "hit_rate": round(rate, 4),
            "prompt_tokens": s["prompt_tokens"],
        })
        for k in agg:
            agg[k] += s[k]

    thit, tmiss = agg["cache_hit_tokens"], agg["cache_miss_tokens"]
    overall = thit / (thit + tmiss) if (thit + tmiss) else 0.0
    return {
        "overall_hit_rate": round(overall, 4),
        "total_requests": agg["requests"],
        "sessions": sessions,
    }


def gather_state() -> dict:
    """聚合所有采集器。单个失败被隔离为该块的 _error，不影响其余面板。"""
    def guard(fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — 面板容错优先
            return {"_error": f"{type(e).__name__}: {e}"}

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git": guard(collect_git),
        "trust": guard(collect_trust),
        "memory": guard(collect_memory),
        "tasks": guard(collect_tasks),
        "gaps": guard(collect_gaps),
        "performance": guard(collect_performance),
        "anomalies": guard(collect_anomalies),
        "tools": guard(collect_tools),
    }
