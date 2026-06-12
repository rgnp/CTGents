"""状态采集 — 只读，复用 src 读函数 / 直接读盘 artifact。

每个采集器经 _guard 包裹：一个数据源坏掉隔离为该块 _error，不拖垮整个视图。
对外是四个 build_*（对应 /api/overview|safety|memory|evolution 四接口）。
缓存命中率刻意直接读 stats/{sid}.json（形状镜像 llm.py _CACHE_STATS），
不 import llm.py——那会把 API 客户端栈拉进监控进程，违背解耦。
其余非平凡解析（frontmatter / gap 缓存 / 门审计）复用 src，避免重写产生漂移。
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATS_DIR = PROJECT_ROOT / "stats"

# session 落盘以 YYYY-MM-DD-… 命名；test-verify.json 等非会话统计无日期前缀，排除。
_SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_session_stem(p: Path) -> bool:
    return bool(_SESSION_RE.match(p.stem)) and not p.stem.endswith("_reflection")

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


def collect_ambitions() -> dict:
    """野心（轻量）：直接解析 ambitions.md，不读 lesson 文件——供总览复用。"""
    from src.tasks import read_ambitions
    sections = _parse_ambitions(read_ambitions())
    active = [s for s in sections if s["section"] not in _DONE_SECTIONS and s["items"]]
    delivered = sum(len(s["items"]) for s in sections if s["section"] in _DONE_SECTIONS)
    return {"sections": sections, "active": active, "delivered": delivered}


def collect_memory() -> dict:
    """记忆 & 野心：lessons（含指纹/遭遇次数，按高频失败排）+ ambitions。"""
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
    amb = collect_ambitions()
    return {
        "count": len(lessons),
        "lessons": lessons,
        "recurring": [x for x in lessons if x["times"] >= 2][:8],
        "ambition_sections": amb["sections"],
        "ambition_active": amb["active"],
        "ambition_delivered": amb["delivered"],
    }


def collect_session() -> dict:
    """当前会话：最新合法 stats stem 即最近会话 id（只读盘，不碰运行态）。"""
    sid = ""
    if STATS_DIR.exists():
        files = sorted(
            (p for p in STATS_DIR.glob("*.json") if _is_session_stem(p)),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if files:
            sid = files[0].stem
    return {"session_id": sid, "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"])}


def collect_current_task() -> dict:
    """当前任务（轻量，不扫归档）——供总览。"""
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


def _archive_title(f: Path, slug: str) -> str:
    """取归档文件首个 `# 标题`，没有则回退到文件名 slug。"""
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            st = line.strip()
            if st.startswith("#"):
                return st.lstrip("# ").strip()
    except Exception:
        pass
    return slug


def collect_tasks() -> dict:
    """任务态：当前任务 + 进度 + 全部归档任务（agent 干过的活）。"""
    from src.config import ARCHIVE_DIR
    from src.tasks import (
        get_task_progress_line,
        has_unfinished,
        is_all_done,
        read_current,
    )

    archived = []
    adir = Path(ARCHIVE_DIR)
    if adir.exists():
        for f in sorted(adir.glob("*.md"), reverse=True):
            parts = f.stem.split("-", 3)
            date = "-".join(parts[:3]) if len(parts) >= 3 else ""
            slug = parts[3] if len(parts) >= 4 else f.stem
            archived.append({"date": date, "slug": slug, "title": _archive_title(f, slug)})

    return {
        "current": read_current(),
        "progress": get_task_progress_line(),
        "unfinished": has_unfinished(),
        "all_done": is_all_done(),
        "archived": archived,
        "archived_count": len(archived),
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
        (p for p in STATS_DIR.glob("*.json") if _is_session_stem(p)),
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
            "tokens": s["prompt_tokens"] + s["completion_tokens"],
        })
        for k in agg:
            agg[k] += s[k]

    thit, tmiss = agg["cache_hit_tokens"], agg["cache_miss_tokens"]
    overall = thit / (thit + tmiss) if (thit + tmiss) else 0.0
    return {
        "overall_hit_rate": round(overall, 4),
        "total_requests": agg["requests"],
        "total_tokens": agg["prompt_tokens"] + agg["completion_tokens"],
        "prompt_tokens": agg["prompt_tokens"],
        "completion_tokens": agg["completion_tokens"],
        "sessions": sessions,
    }


def collect_checks() -> dict:
    """检查状态：pre-commit 由门审计推导；test/lint 无落盘 artifact 则 unknown。

    刻意不主动跑测试/lint（面板只读、不触发行为）。门审计是唯一有真实落盘证据的检查。
    """
    pre = "pass" if collect_trust().get("ok") else "alert"
    return {
        "pre_commit": pre,
        "test": "unknown",
        "lint": "unknown",
        "note": "测试/lint 无落盘状态 artifact；面板只读，不主动运行",
    }


def _modified_protected() -> list[str]:
    """受保护文件里被改动（未提交）的——风险信号，非阻断。"""
    from src.guard import PROTECTED_FILES
    mods = []
    for line in _git(["status", "--porcelain"]).splitlines():
        path = line[3:].strip()
        if path and str((PROJECT_ROOT / path).resolve()) in PROTECTED_FILES:
            mods.append(path)
    return mods


def _hit_rate_drop(threshold: float = 0.05) -> str:
    """最近一会话命中率较前序均值的跌幅（>=threshold 才报）。"""
    sess = collect_performance().get("sessions", [])
    if len(sess) < 4:
        return ""
    latest, prior = sess[0]["hit_rate"], [s["hit_rate"] for s in sess[1:]]
    avg = sum(prior) / len(prior)
    if avg - latest >= threshold:
        return f"最近 {latest * 100:.1f}% vs 前均 {avg * 100:.1f}%"
    return ""


def collect_risks() -> dict:
    """风险提示：门禁失败 / 受保护文件改动 / 命中率下滑 / 高频失败模式。"""
    risks: list[dict] = []
    trust = collect_trust()
    if not trust.get("ok"):
        risks.append({"level": "high", "kind": "门审计失败", "detail": str(trust.get("gate", ""))[:140]})
    for f in _modified_protected():
        risks.append({"level": "high", "kind": "受保护文件改动", "detail": f})
    drop = _hit_rate_drop()
    if drop:
        risks.append({"level": "med", "kind": "命中率下滑", "detail": drop})
    for lesson in collect_memory().get("recurring", []):
        if lesson.get("times", 0) >= 5:
            risks.append({
                "level": "med", "kind": "高频失败",
                "detail": f"{lesson.get('fingerprint') or lesson.get('name')} ×{lesson['times']}",
            })
    return {"risks": risks, "count": len(risks)}


def _guard(fn):
    """单个采集器失败被隔离为该块的 _error，不拖垮整个视图。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — 面板容错优先
        return {"_error": f"{type(e).__name__}: {e}"}


def build_overview() -> dict:
    """/api/overview — 会话 + 当前任务 + 野心摘要 + 缓存性能 + 健康判定输入。"""
    perf = _guard(collect_performance)
    anom = _guard(collect_anomalies)
    trust = _guard(collect_trust)
    acount = sum(len(r.get("anomalies", [])) for r in anom.get("recent", []))
    return {
        "generated_at": _now(),
        "session": _guard(collect_session),
        "task": _guard(collect_current_task),
        "ambitions": _guard(collect_ambitions),
        "performance": perf,
        "health": {
            "gate_ok": trust.get("ok", True),
            "anomaly_count": acount,
            "hit_rate": perf.get("overall_hit_rate", 0.0),
        },
    }


def build_safety() -> dict:
    """/api/safety — 门审计 + 检查状态 + 风险提示。"""
    return {
        "generated_at": _now(),
        "trust": _guard(collect_trust),
        "checks": _guard(collect_checks),
        "risks": _guard(collect_risks),
    }


def build_memory() -> dict:
    """/api/memory — lessons（含指纹/频次）+ 野心 + 自反思异常。"""
    return {
        "generated_at": _now(),
        "memory": _guard(collect_memory),
        "anomalies": _guard(collect_anomalies),
    }


def build_evolution() -> dict:
    """/api/evolution — git 时间线 + 全部任务 + 工具基线 + 改进方向。"""
    return {
        "generated_at": _now(),
        "git": _guard(collect_git),
        "tasks": _guard(collect_tasks),
        "tools": _guard(collect_tools),
        "gaps": _guard(collect_gaps),
    }
