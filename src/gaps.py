"""差距检测框架 — 多信号源汇聚，统一为可排序的改进方向。

两层递进：
  Layer 1 (diagnostics.py)  — 单信号->翻译
  Layer 2 (gaps.py)         — 多信号汇聚->去噪->排序->优选方向   <- 本模块
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .paths import CORE_ROOT, TASKS_DIR, WORKSPACE_ROOT

PROJECT_ROOT = CORE_ROOT
_GAP_CACHE_FILE = WORKSPACE_ROOT / ".gap_cache.json"
_GAP_LEDGER_FILE = TASKS_DIR / "gap-ledger.json"
_GAP_STATUSES = frozenset({"discovered", "accepted", "rejected", "deferred", "fixed", "verified"})
_BENEFIT_WINDOW_SESSIONS = 3


def _git_tree_hash() -> str:
    """返回完整工作区指纹；保留旧函数名以兼容现有调用和测试。"""
    try:
        from .verification_receipts import workspace_fingerprint

        return workspace_fingerprint(PROJECT_ROOT)
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
    signal_key: str = ""

    @property
    def id(self) -> str:
        """稳定身份：同一来源/类型/文件的 gap 跨扫描保持同 ID。"""
        files = "|".join(sorted(path.replace("\\", "/") for path in self.affected_files))
        fallback = " ".join(self.detail.lower().split()) if not files else ""
        raw = f"{self.source}|{self.gap_type}|{files}|{self.signal_key}|{fallback}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass
class GapEvent:
    status: str
    timestamp: str
    note: str = ""
    workspace_fingerprint: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class GapRecord:
    gap_id: str
    status: str
    source: str
    gap_type: str
    detail: str
    affected_files: list[str] = field(default_factory=list)
    suggestion: str = ""
    first_seen: str = ""
    last_seen: str = ""
    history: list[GapEvent] = field(default_factory=list)
    signal_key: str = ""


@dataclass
class GapReport:
    gaps: list[Gap] = field(default_factory=list)
    sources_scanned: int = 0
    sources_failed: int = 0
    failures: list[str] = field(default_factory=list)


# 报告缓存：detect_all_gaps 自动存储，供 /fix 指令和 get_last_report 取用
_LAST_REPORT: GapReport | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_gap_ledger() -> dict[str, GapRecord]:
    if not _GAP_LEDGER_FILE.exists():
        return {}
    try:
        raw = json.loads(_GAP_LEDGER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records: dict[str, GapRecord] = {}
    for item in raw.get("records", []):
        try:
            record_data = dict(item)
            history = [GapEvent(**event) for event in record_data.pop("history", [])]
            record = GapRecord(**record_data, history=history)
        except (TypeError, AttributeError):
            continue
        if record.status in _GAP_STATUSES:
            records[record.gap_id] = record
    return records


def _save_gap_ledger(records: dict[str, GapRecord]) -> None:
    payload = {
        "version": 1,
        "records": [asdict(records[key]) for key in sorted(records)],
    }
    _GAP_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = _GAP_LEDGER_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(_GAP_LEDGER_FILE)


def _event(
    status: str,
    note: str = "",
    fingerprint: str = "",
    evidence: dict | None = None,
) -> GapEvent:
    return GapEvent(
        status=status,
        timestamp=_now(),
        note=note,
        workspace_fingerprint=fingerprint,
        evidence=evidence or {},
    )


def sync_gap_ledger(report: GapReport) -> dict[str, GapRecord]:
    """把扫描结果并入台账；保留人工决策状态，只刷新事实描述和 last_seen。"""
    records = _load_gap_ledger()
    changed = False
    now = _now()
    for gap in report.gaps:
        record = records.get(gap.id)
        if record is None:
            records[gap.id] = GapRecord(
                gap_id=gap.id,
                status="discovered",
                source=gap.source,
                gap_type=gap.gap_type,
                detail=gap.detail,
                affected_files=list(gap.affected_files),
                suggestion=gap.suggestion,
                first_seen=now,
                last_seen=now,
                history=[_event("discovered", "首次检测")],
                signal_key=gap.signal_key,
            )
            changed = True
            continue
        fields_changed = (
            record.detail != gap.detail
            or record.affected_files != gap.affected_files
            or record.suggestion != gap.suggestion
            or record.signal_key != gap.signal_key
            or record.last_seen != now
        )
        record.detail = gap.detail
        record.affected_files = list(gap.affected_files)
        record.suggestion = gap.suggestion
        record.signal_key = gap.signal_key
        record.last_seen = now
        if record.status == "verified" and record.source != "performance":
            record.status = "accepted"
            record.history.append(
                _event("accepted", "回归：已验证 gap 再次被同一信号检出", _git_tree_hash())
            )
            fields_changed = True
        changed = changed or fields_changed
    for record in records.values():
        if record.status != "verified" or record.source != "performance" or not record.signal_key:
            continue
        verified_event = next(
            (event for event in reversed(record.history) if event.status == "verified"),
            None,
        )
        if verified_event is None:
            continue
        evidence = _performance_signal_window(record.signal_key, since=verified_event.timestamp)
        if not evidence.get("occurrences", 0):
            continue
        record.status = "accepted"
        record.history.append(
            _event(
                "accepted",
                "回归：verified 之后的会话再次出现同类异常",
                _git_tree_hash(),
                evidence,
            )
        )
        changed = True
    if changed:
        _save_gap_ledger(records)
    return records


def get_gap_record(reference: str | int) -> GapRecord | None:
    """按最近报告编号、完整 ID 或唯一 ID 前缀解析台账记录。"""
    records = _load_gap_ledger()
    if isinstance(reference, int) or str(reference).isdigit():
        gap = get_gap_by_index(int(reference))
        return records.get(gap.id) if gap is not None else None
    ref = str(reference).strip().lower()
    if ref in records:
        return records[ref]
    matches = [record for gap_id, record in records.items() if gap_id.startswith(ref)]
    return matches[0] if len(matches) == 1 else None


_TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset({"accepted", "rejected", "deferred"}),
    "accepted": frozenset({"rejected", "deferred", "fixed"}),
    "rejected": frozenset({"accepted", "deferred"}),
    "deferred": frozenset({"accepted", "rejected"}),
    "fixed": frozenset({"accepted"}),
    "verified": frozenset({"accepted"}),
}


def set_gap_status(reference: str | int, status: str, note: str = "") -> str:
    """执行人工生命周期决策；verified 只能由 verify_gap 机械产生。"""
    target = status.strip().lower()
    if target not in _GAP_STATUSES or target in {"discovered", "verified"}:
        return f"❌ 不允许直接设置状态：{status}"
    record = get_gap_record(reference)
    if record is None:
        return f"❌ 找不到 gap：{reference}"
    if target == record.status:
        return f"gap {record.gap_id} 已是 {target}。"
    if target not in _TRANSITIONS.get(record.status, frozenset()):
        return f"❌ 非法状态迁移：{record.status} → {target}"
    records = _load_gap_ledger()
    current = records[record.gap_id]
    current.status = target
    fingerprint = _git_tree_hash() if target == "fixed" else ""
    evidence: dict = {}
    if target == "fixed" and current.source == "performance" and current.signal_key:
        evidence = _performance_signal_window(current.signal_key)
    current.history.append(_event(target, note, fingerprint, evidence))
    _save_gap_ledger(records)
    return f"✅ gap {record.gap_id}: {record.status} → {target}" + (f"（{note}）" if note else "")


def verify_gap(reference: str | int) -> str:
    """重新扫描原信号：fixed gap 消失才进入 verified，仍存在则保持 fixed。"""
    record = get_gap_record(reference)
    if record is None:
        return f"❌ 找不到 gap：{reference}"
    if record.status != "fixed":
        return f"❌ gap {record.gap_id} 当前是 {record.status}，只有 fixed 可复核。"
    if record.source == "performance" and record.signal_key:
        return _verify_performance_gap(record)
    report = detect_all_gaps(top_n=100, force=True)
    failed_sources = {failure.split(":", 1)[0].strip() for failure in report.failures}
    if report.sources_scanned == 0 or record.source in failed_sources:
        records = _load_gap_ledger()
        current = records[record.gap_id]
        current.history.append(
            _event("fixed", f"复核中止：检测源 {record.source} 不可用", _git_tree_hash())
        )
        _save_gap_ledger(records)
        return f"❌ gap {record.gap_id} 无法复核：检测源 {record.source} 本次扫描失败。"
    still_present = next((gap for gap in report.gaps if gap.id == record.gap_id), None)
    records = _load_gap_ledger()
    current = records[record.gap_id]
    fingerprint = _git_tree_hash()
    if still_present is not None:
        current.history.append(_event("fixed", "复核失败：同一 gap 仍可检测到", fingerprint))
        _save_gap_ledger(records)
        return f"❌ gap {record.gap_id} 复核失败：修复后仍可检测到，状态保持 fixed。"
    current.status = "verified"
    current.history.append(_event("verified", "复核通过：原信号已消失", fingerprint))
    _save_gap_ledger(records)
    return f"✅ gap {record.gap_id} 已复核通过：fixed → verified"


def _performance_signal_window(signal_key: str, *, since: str | None = None) -> dict:
    from .tracker import get_anomaly_signal_window

    tool, separator, anomaly_type = signal_key.partition("|")
    if not separator or not tool or not anomaly_type:
        return {}
    return get_anomaly_signal_window(
        tool,
        anomaly_type,
        since=since,
        limit=_BENEFIT_WINDOW_SESSIONS,
    )


def _verify_performance_gap(record: GapRecord) -> str:
    fixed_event = next(
        (event for event in reversed(record.history) if event.status == "fixed"),
        None,
    )
    if fixed_event is None:
        return f"❌ gap {record.gap_id} 缺少 fixed 基线，无法复核。"
    evidence = _performance_signal_window(record.signal_key, since=fixed_event.timestamp)
    observed = evidence.get("sessions_observed", 0)
    if observed < _BENEFIT_WINDOW_SESSIONS:
        return (
            f"⏳ gap {record.gap_id} 仍在观察："
            f"{observed}/{_BENEFIT_WINDOW_SESSIONS} 个有效后续会话。"
        )

    records = _load_gap_ledger()
    current = records[record.gap_id]
    fingerprint = _git_tree_hash()
    if evidence.get("occurrences", 0):
        current.status = "accepted"
        current.history.append(
            _event(
                "accepted",
                "收益复核失败：观察窗口内同类异常复发",
                fingerprint,
                evidence,
            )
        )
        _save_gap_ledger(records)
        return (
            f"❌ gap {record.gap_id} 收益复核失败："
            f"{evidence['occurrences']}/{observed} 个有效会话复发，已重开为 accepted。"
        )

    current.status = "verified"
    current.history.append(
        _event(
            "verified",
            "收益复核通过：连续有效会话未出现同类异常",
            fingerprint,
            evidence,
        )
    )
    _save_gap_ledger(records)
    return (
        f"✅ gap {record.gap_id} 收益复核通过："
        f"连续 {observed} 个有效会话无同类异常，fixed → verified"
    )


def format_gap_ledger() -> str:
    records = _load_gap_ledger()
    if not records:
        return "gap 台账为空，先运行 /pulse。"
    order = {"accepted": 0, "fixed": 1, "discovered": 2, "deferred": 3, "rejected": 4, "verified": 5}
    items = sorted(records.values(), key=lambda record: (order.get(record.status, 9), record.gap_id))
    lines = ["可靠性 gap 台账", ""]
    for record in items:
        files = ", ".join(record.affected_files) or "-"
        lines.append(f"- {record.gap_id} [{record.status}] {record.detail}")
        lines.append(f"  文件: {files} | 最近发现: {record.last_seen}")
        if record.status == "fixed" and record.source == "performance" and record.signal_key:
            fixed_event = next(
                (event for event in reversed(record.history) if event.status == "fixed"),
                None,
            )
            if fixed_event is not None:
                evidence = _performance_signal_window(
                    record.signal_key,
                    since=fixed_event.timestamp,
                )
                lines.append(
                    f"  收益观察: {evidence.get('sessions_observed', 0)}/"
                    f"{_BENEFIT_WINDOW_SESSIONS} 个有效后续会话"
                )
    return "\n".join(lines)


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
        f"【主动进化 · 方向 #{index} · gap {gap.id}】{gap.detail}\n\n"
        f"来源: {gap.source} | 严重度: {gap.severity} | 置信度: {gap.confidence:.0%}\n"
        f"涉及文件: {files}\n"
        f"建议: {gap.suggestion}\n\n"
        f"请推进这个改进方向。先搜方案、读代码、定做法，然后改、测、提交。"
        f"判断权在你——不是所有建议都该照做，读代码后会知道什么合理。"
    )


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
                signal_key=f"{a['tool']}|{a['type']}",
            ))
    gaps.sort(key=lambda g: g.confidence * (1.5 if g.actionable else 0.5), reverse=True)
    return _deduplicate(gaps)


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
    return _deduplicate(gaps)


# 覆盖率 gap 信号已移除（2026-06-13）：项目已论证否决"覆盖率=指标"（连门禁一起拆）。
# 它只会把 agent 推向按 % 刷/排序（Goodhart：覆盖率测执行不测验证），还要常驻跑
# coverage report → 制造 thrash + 数据易陈旧。"哪条核心路径裸奔"该在真要动它时一次性
# 查（pytest --cov=那块），不做常驻驱动信号。


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


def detect_all_gaps(top_n: int = 5, *, force: bool = False) -> GapReport:
    global _LAST_REPORT
    tree = _git_tree_hash()
    cached = None if force else _load_gap_cache(tree)
    if cached is not None:
        _LAST_REPORT = cached
        sync_gap_ledger(cached)
        return cached
    report = GapReport()
    detectors: list[tuple[str, Callable[[], list[Gap]]]] = [
        ("performance", _detect_performance_gaps),
        ("static", _detect_static_gaps),
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
    sync_gap_ledger(report)
    return report


def _static_suggestion(category: str) -> str:
    return {
        "dead_code": "Delete unused function/class, or mark as registration pattern.",
        "complexity": "Extract sub-functions, use early returns, dict over long if-elif.",
        "anti_pattern": "Fix bare except / mutable defaults / swallowed exceptions.",
        "style": "Extract responsibilities; use dataclass for many params.",
    }.get(category, "Review and fix.")

_SOURCE_LABELS = {"performance": "性能", "static": "静态分析"}
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
        record = get_gap_record(g.id)
        status = record.status if record is not None else "discovered"
        lines.append(f"  #{i} {icon} [{src}] [{status}] {g.detail}")
        lines.append(f"     ID: {g.id}")
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
    lines.append("用 /fix N 接受并处理；用 /gap 查看或更新生命周期状态。")
    return "\n".join(lines)
