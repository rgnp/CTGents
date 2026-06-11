"""差距检测框架 — 多信号源汇聚，统一为可排序的改进方向。

三层递进：
  Layer 1 (diagnostics.py)  — 单信号->翻译
  Layer 2 (gaps.py)         — 多信号汇聚->去噪->排序->优选方向   <- 本模块
  Layer 3 (outcome.py)      — 收到方向后，搜标准->定标准->执行闭环
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GAP_CACHE_FILE = PROJECT_ROOT / ".gap_cache.json"


def _git_tree_hash() -> str:
    """返回当前 HEAD 的 tree hash；失败返回空串（禁用缓存）。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            capture_output=True, text=True, timeout=3, cwd=PROJECT_ROOT,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _load_gap_cache(tree: str) -> GapReport | None:
    if not tree or not _GAP_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_GAP_CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("tree") != tree:
            return None
        gaps = [Gap(**g) for g in data["report"]["gaps"]]
        return GapReport(
            gaps=gaps,
            sources_scanned=data["report"]["sources_scanned"],
            sources_failed=data["report"]["sources_failed"],
            failures=data["report"]["failures"],
        )
    except Exception:
        return None


def _save_gap_cache(tree: str, report: GapReport) -> None:
    if not tree:
        return
    with contextlib.suppress(Exception):
        _GAP_CACHE_FILE.write_text(
            json.dumps({"tree": tree, "report": asdict(report)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class Gap:
    source: str
    gap_type: str
    severity: str
    detail: str
    affected_files: list[str] = field(default_factory=list)
    suggestion: str = ""
    confidence: float = 0.0
    actionable: bool = True


@dataclass
class GapReport:
    gaps: list[Gap] = field(default_factory=list)
    sources_scanned: int = 0
    sources_failed: int = 0
    failures: list[str] = field(default_factory=list)


# 报告缓存：detect_all_gaps 自动存储，供 /fix 指令和 get_last_report 取用
_LAST_REPORT: GapReport | None = None


def get_last_report() -> GapReport | None:
    """返回最近一次 detect_all_gaps 的报告。"""
    return _LAST_REPORT


def get_gap_by_index(n: int) -> Gap | None:
    """按 1-based 编号取 gap。不存在返回 None。"""
    report = _LAST_REPORT
    if report is None or n < 1 or n > len(report.gaps):
        return None
    return report.gaps[n - 1]


def _make_fix_prompt(gap: Gap, index: int) -> str:
    """把 gap 翻译成 agent 可行动的任务 prompt。

    不预设步骤——只给方向/文件/建议，让 agent 自己判断怎么做。
    """
    files = ", ".join(gap.affected_files) if gap.affected_files else "（需自行定位）"
    return (
        f"【主动进化 · 方向 #{index}】{gap.detail}\n\n"
        f"来源: {gap.source} | 严重度: {gap.severity} | 置信度: {gap.confidence:.0%}\n"
        f"涉及文件: {files}\n"
        f"建议: {gap.suggestion}\n\n"
        f"请推进这个改进方向。先搜方案、读代码、定做法，然后改、测、提交。"
        f"判断权在你——不是所有建议都该照做，读代码后会知道什么合理。"
    )


_PER_SOURCE_MAX = 3


def _detect_performance_gaps() -> list[Gap]:
    from .tracker import _discover_sessions, detect_anomalies, get_cross_session_baseline
    sessions = _discover_sessions()
    if not sessions:
        return []
    baseline = get_cross_session_baseline()
    gaps: list[Gap] = []
    seen: set[tuple[str, str]] = set()
    for sid in sessions[:5]:
        for a in detect_anomalies(sid, baseline):
            key = (a["tool"], a["type"])
            if key in seen:
                continue
            seen.add(key)
            from .diagnostics import diagnose_one
            d = diagnose_one(a)
            sev = {"crit": "high", "warn": "medium"}.get(a.get("severity", "warn"), "medium")
            gaps.append(Gap(
                source="performance",
                gap_type=f"{a['type']}_{d.root_pattern}",
                severity=sev,
                detail=f"{a['detail']} -> {d.likely_cause}",
                affected_files=d.affected_files,
                suggestion=d.suggested_action,
                confidence=d.confidence,
                actionable=d.actionable,
            ))
    gaps.sort(key=lambda g: g.confidence * (1.5 if g.actionable else 0.5), reverse=True)
    return _deduplicate(gaps)[:_PER_SOURCE_MAX]


def _detect_static_gaps() -> list[Gap]:
    from .tools.analyzer import ProjectAnalyzer
    try:
        analyzer = ProjectAnalyzer(PROJECT_ROOT)
        report = analyzer.analyze(include_tests=False)
    except Exception:
        return []
    gaps: list[Gap] = []
    for f in report.findings:
        if f.severity != "high":
            continue
        msg_lower = f.message.lower()
        file_norm = f.file.replace("\\", "/")
        if "/tools/" in file_norm and "execute" in msg_lower:
            continue
        if "_auto_reload_module" in f.message:
            continue
        try:
            rel = Path(f.file).resolve().relative_to(PROJECT_ROOT.resolve())
            rel_path = str(rel)
        except ValueError:
            rel_path = f.file
        gaps.append(Gap(
            source="static", gap_type=f.category, severity=f.severity,
            detail=f"{rel_path}:{f.line} - {f.message}",
            affected_files=[rel_path],
            suggestion=_static_suggestion(f.category),
            confidence=0.90, actionable=True,
        ))
    priority = {"dead_code": 4, "anti_pattern": 3, "complexity": 2, "style": 1}
    gaps.sort(key=lambda g: priority.get(g.gap_type, 0), reverse=True)
    return _deduplicate(gaps)[:_PER_SOURCE_MAX]


def _detect_coverage_gaps() -> list[Gap]:
    cov_file = PROJECT_ROOT / ".coverage"
    if not cov_file.exists():
        return []
    try:
        import subprocess
        result = subprocess.run(
            ["coverage", "report", "--format=markdown"],
            capture_output=True, text=True, timeout=15, cwd=str(PROJECT_ROOT),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    gaps: list[Gap] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[0]
        if not name.startswith("src/"):
            continue
        try:
            stmts = int(parts[1])
            missed = int(parts[2])
            cov_pct = int(parts[3].rstrip("%"))
        except (ValueError, IndexError):
            continue
        if stmts < 5:
            continue
        if cov_pct < 50:
            severity = "high"
        elif cov_pct < 80:
            severity = "medium"
        else:
            continue
        gaps.append(Gap(
            source="coverage", gap_type="low_coverage", severity=severity,
            detail=f"{name}: {cov_pct}% ({missed}/{stmts} uncovered)",
            affected_files=[name],
            suggestion=f"Add tests for {name}, at least critical paths.",
            confidence=0.95, actionable=True,
        ))
    gaps.sort(key=lambda g: 0 if g.severity == "high" else 1)
    return _deduplicate(gaps)[:_PER_SOURCE_MAX]


_SEVERITY_WEIGHT = {"crit": 4, "high": 3, "medium": 2, "low": 1}


def _gap_score(g: Gap) -> float:
    return _SEVERITY_WEIGHT.get(g.severity, 1) * g.confidence * (1.5 if g.actionable else 0.5)


def _deduplicate(gaps: list[Gap]) -> list[Gap]:
    seen: set[tuple[str, str]] = set()
    result: list[Gap] = []
    for g in gaps:
        if not g.affected_files:
            result.append(g)
            continue
        for f in g.affected_files:
            key = (f, g.gap_type)
            if key not in seen:
                seen.add(key)
                result.append(g)
                break
    return result


def _prioritize(gaps: list[Gap], top_n: int = 5) -> list[Gap]:
    return _deduplicate(sorted(gaps, key=_gap_score, reverse=True))[:top_n]


def detect_all_gaps(top_n: int = 5) -> GapReport:
    global _LAST_REPORT
    tree = _git_tree_hash()
    cached = _load_gap_cache(tree)
    if cached is not None:
        _LAST_REPORT = cached
        return cached
    report = GapReport()
    detectors: list[tuple[str, Callable[[], list[Gap]]]] = [
        ("performance", _detect_performance_gaps),
        ("static", _detect_static_gaps),
        ("coverage", _detect_coverage_gaps),
    ]
    for name, detector in detectors:
        report.sources_scanned += 1
        try:
            report.gaps.extend(detector())
        except Exception as e:
            report.sources_failed += 1
            report.failures.append(f"{name}: {e}")
    report.gaps = _prioritize(report.gaps, top_n=top_n)
    _LAST_REPORT = report
    _save_gap_cache(tree, report)
    return report


def _static_suggestion(category: str) -> str:
    return {
        "dead_code": "Delete unused function/class, or mark as registration pattern.",
        "complexity": "Extract sub-functions, use early returns, dict over long if-elif.",
        "anti_pattern": "Fix bare except / mutable defaults / swallowed exceptions.",
        "style": "Extract responsibilities; use dataclass for many params.",
    }.get(category, "Review and fix.")

_SOURCE_LABELS = {"performance": "性能", "static": "静态分析", "coverage": "覆盖率"}
_SEV_ICONS = {"crit": "!!", "high": "!!", "medium": "! ", "low": "~ "}


def format_gap_report(report: GapReport) -> str:
    if not report.gaps:
        msg = "未发现值得关注的改进方向。"
        if report.failures:
            msg += f"（{report.sources_failed}/{report.sources_scanned} 个信号源失败）"
        return msg
    lines = ["主动进化 · 方向发现"]
    lines.append(f"  扫描 {report.sources_scanned} 个信号源，"
                 f"发现 {len(report.gaps)} 个优先方向：")
    lines.append("")
    for i, g in enumerate(report.gaps, 1):
        icon = _SEV_ICONS.get(g.severity, "? ")
        src = _SOURCE_LABELS.get(g.source, g.source)
        files = ", ".join(g.affected_files) if g.affected_files else "-"
        lines.append(f"  #{i} {icon} [{src}] {g.detail}")
        lines.append(f"     文件: {files}")
        if g.suggestion:
            lines.append(f"     建议: {g.suggestion}")
        lines.append(f"     置信度: {g.confidence:.0%} | "
                     f"{'可修' if g.actionable else '需进一步分析'}")
    if report.sources_failed:
        lines.append("")
        lines.append(f"  {report.sources_failed}/{report.sources_scanned} 信号源失败：")
        for f in report.failures:
            lines.append(f"     - {f}")
    lines.append("")
    lines.append("说 '处理 #N' 着手某个方向，或 '全做' 逐个推进。")
    return "\n".join(lines)
