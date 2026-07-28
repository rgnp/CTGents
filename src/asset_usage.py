"""Auditable retrieval, adoption, outcome, and explicit-value events."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .paths import TASKS_DIR

USAGE_FILE = TASKS_DIR / "asset-usage.jsonl"
MAX_EVENTS = 2000
_KINDS = frozenset({"memory", "knowledge"})
_lock = threading.Lock()


@dataclass(frozen=True)
class AssetUsageEvent:
    event_id: str
    stage: str
    asset_kind: str
    asset_id: str
    timestamp: str
    session_id: str
    task_key: str
    query: str = ""
    purpose: str = ""
    outcome: str = ""
    evidence: str = ""
    adoption_id: str = ""
    feedback: str = ""
    reason: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _current_session_id() -> str:
    try:
        from .tracker import current_session

        return current_session() or "local"
    except Exception:
        return "local"


def current_task_key(text: str | None = None) -> str:
    try:
        if text is None:
            from .tasks import read_current

            text = read_current()
    except Exception:
        return ""
    source = (text or "").strip()
    if not source:
        return ""
    title = next(
        (
            line.lstrip("# ").strip()
            for line in source.splitlines()
            if line.startswith("#") and line.lstrip("# ").strip()
        ),
        "",
    )
    if not title:
        return ""
    slug = re.sub(r"[^\w一-鿿]+", "-", title).strip("-").lower()
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]


def _read_events() -> list[AssetUsageEvent]:
    if not USAGE_FILE.exists():
        return []
    try:
        lines = USAGE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[AssetUsageEvent] = []
    for line in lines:
        try:
            events.append(AssetUsageEvent(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return events


def _append_events(new_events: list[AssetUsageEvent]) -> None:
    if not new_events:
        return
    with _lock:
        events = (_read_events() + new_events)[-MAX_EVENTS:]
        try:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp = USAGE_FILE.with_suffix(".tmp")
            temp.write_text(
                "".join(json.dumps(asdict(event), ensure_ascii=False) + "\n" for event in events),
                encoding="utf-8",
            )
            temp.replace(USAGE_FILE)
        except OSError:
            return


def record_retrieval(asset_kind: str, asset_ids: list[str], query: str) -> None:
    """Record assets actually returned to the model, not all search candidates."""
    if asset_kind not in _KINDS:
        return
    session_id = _current_session_id()
    task_key = current_task_key()
    timestamp = _now()
    events = [
        AssetUsageEvent(
            event_id=uuid.uuid4().hex,
            stage="retrieved",
            asset_kind=asset_kind,
            asset_id=asset_id,
            timestamp=timestamp,
            session_id=session_id,
            task_key=task_key,
            query=query.strip(),
        )
        for asset_id in dict.fromkeys(asset_ids)
        if asset_id.strip()
    ]
    _append_events(events)


def adopt_asset(asset_kind: str, asset_id: str, purpose: str) -> str:
    """Explicitly adopt a recently retrieved asset for the current task."""
    kind = asset_kind.strip().lower()
    identifier = asset_id.strip()
    reason = purpose.strip()
    if kind not in _KINDS:
        return f"❌ 未知资产类型：{asset_kind}"
    if not identifier or not reason:
        return "❌ adopt_asset 需要 asset_id 和具体 purpose。"
    session_id = _current_session_id()
    task_key = current_task_key()
    events = _read_events()
    latest_outcome = {
        event.adoption_id: event.outcome
        for event in events
        if event.stage == "outcome" and event.adoption_id
    }
    retrieval = next(
        (
            event
            for event in reversed(events)
            if event.stage == "retrieved"
            and event.asset_kind == kind
            and event.asset_id == identifier
            and event.session_id == session_id
        ),
        None,
    )
    if retrieval is None:
        return f"❌ 本会话尚未检索到 {kind}:{identifier}，不能声明采用。"
    existing = next(
        (
            event
            for event in reversed(events)
            if event.stage == "adopted"
            and event.asset_kind == kind
            and event.asset_id == identifier
            and event.session_id == session_id
            and event.task_key == task_key
            and latest_outcome.get(event.event_id) not in {"passed", "abandoned"}
        ),
        None,
    )
    if existing is not None:
        return f"资产已采用：{kind}:{identifier}（event {existing.event_id[:8]}）"
    event = AssetUsageEvent(
        event_id=uuid.uuid4().hex,
        stage="adopted",
        asset_kind=kind,
        asset_id=identifier,
        timestamp=_now(),
        session_id=session_id,
        task_key=task_key,
        purpose=reason,
        evidence=f"retrieval:{retrieval.event_id}",
    )
    _append_events([event])
    scope = f"任务 {task_key}" if task_key else "当前会话（无活跃任务）"
    return (
        f"✅ 已采用 {kind}:{identifier}（adoption {event.event_id[:8]}）"
        f"用于{scope}：{reason}"
    )


def record_task_outcome(
    outcome: str,
    evidence: str,
    *,
    task_key: str | None = None,
) -> int:
    """Attach a deterministic task result to assets explicitly adopted for that task."""
    status = outcome.strip().lower()
    if status not in {"passed", "failed", "abandoned"}:
        return 0
    key = current_task_key() if task_key is None else task_key
    if not key:
        return 0
    events = _read_events()
    adoptions = [
        event for event in events if event.stage == "adopted" and event.task_key == key
    ]
    latest_outcome: dict[str, str] = {}
    for event in events:
        if event.stage == "outcome" and event.adoption_id:
            latest_outcome[event.adoption_id] = event.outcome
    new_events: list[AssetUsageEvent] = []
    for adoption in adoptions:
        previous = latest_outcome.get(adoption.event_id)
        if previous == status or previous in {"passed", "abandoned"}:
            continue
        new_events.append(
            AssetUsageEvent(
                event_id=uuid.uuid4().hex,
                stage="outcome",
                asset_kind=adoption.asset_kind,
                asset_id=adoption.asset_id,
                timestamp=_now(),
                session_id=_current_session_id(),
                task_key=key,
                outcome=status,
                evidence=evidence.strip()[-1000:],
                adoption_id=adoption.event_id,
            )
        )
    _append_events(new_events)
    return len(new_events)


def feedback_asset(
    asset_kind: str,
    asset_id: str,
    verdict: str,
    reason: str,
    adoption_id: str = "",
) -> str:
    """Attach an explicit value judgment to one adoption that already has an outcome."""
    kind = asset_kind.strip().lower()
    identifier = asset_id.strip()
    judgment = verdict.strip().lower()
    explanation = reason.strip()
    adoption_ref = adoption_id.strip()
    if kind not in _KINDS:
        return f"❌ 未知资产类型：{asset_kind}"
    if judgment not in {"helpful", "misleading"}:
        return f"❌ 未知反馈类型：{verdict}"
    if not identifier or not explanation:
        return "❌ feedback_asset 需要 asset_id 和具体 reason。"

    events = _read_events()
    outcome_by_adoption: dict[str, AssetUsageEvent] = {}
    for event in events:
        if event.stage == "outcome" and event.adoption_id:
            outcome_by_adoption[event.adoption_id] = event
    candidates = [
        event
        for event in events
        if event.stage == "adopted"
        and event.asset_kind == kind
        and event.asset_id == identifier
        and event.event_id in outcome_by_adoption
        and (not adoption_ref or event.event_id.startswith(adoption_ref))
    ]
    if not candidates:
        scope = f"（adoption_id={adoption_ref}）" if adoption_ref else ""
        return f"❌ 没有找到已有任务结果的采用记录：{kind}:{identifier}{scope}"
    if adoption_ref and len(candidates) > 1:
        return "❌ adoption_id 前缀不唯一，请提供更长的事件 ID。"
    adoption = candidates[-1]
    previous = next(
        (
            event
            for event in reversed(events)
            if event.stage == "feedback" and event.adoption_id == adoption.event_id
        ),
        None,
    )
    if previous is not None and previous.feedback == judgment:
        return (
            f"资产已有相同反馈：{kind}:{identifier} → {judgment}"
            f"（event {previous.event_id[:8]}）"
        )
    outcome = outcome_by_adoption[adoption.event_id]
    event = AssetUsageEvent(
        event_id=uuid.uuid4().hex,
        stage="feedback",
        asset_kind=kind,
        asset_id=identifier,
        timestamp=_now(),
        session_id=_current_session_id(),
        task_key=adoption.task_key,
        feedback=judgment,
        reason=explanation,
        evidence=f"outcome:{outcome.event_id}",
        adoption_id=adoption.event_id,
    )
    _append_events([event])
    action = "确认有帮助" if judgment == "helpful" else "标记有误导"
    return (
        f"✅ 已{action}：{kind}:{identifier}"
        f"（adoption {adoption.event_id[:8]}）— {explanation}"
    )


def format_usage_summary(asset_kind: str) -> str:
    kind = asset_kind.strip().lower()
    events = [event for event in _read_events() if event.asset_kind == kind]
    retrieved = {event.asset_id for event in events if event.stage == "retrieved"}
    adoptions = [event for event in events if event.stage == "adopted"]
    latest: dict[str, str] = {}
    for event in events:
        if event.stage == "outcome" and event.adoption_id:
            latest[event.adoption_id] = event.outcome
    feedback: dict[str, str] = {}
    for event in events:
        if event.stage == "feedback" and event.adoption_id:
            feedback[event.adoption_id] = event.feedback
    outcomes = list(latest.values())
    unresolved = sum(1 for event in adoptions if event.event_id not in latest)
    judged = list(feedback.values())
    lines = [
        f"资产使用: 检索 {len(retrieved)} 项，明确采用 {len(adoptions)} 次，"
        f"参与通过任务 {outcomes.count('passed')} 次，失败 {outcomes.count('failed')} 次，"
        f"放弃 {outcomes.count('abandoned')} 次，待结果 {unresolved} 次；"
        f"显式 helpful {judged.count('helpful')} 次，misleading {judged.count('misleading')} 次。"
    ]

    misleading_assets = sorted(
        {
            event.asset_id
            for event in adoptions
            if feedback.get(event.event_id) == "misleading"
        }
    )
    pending_feedback = sorted(
        f"{event.asset_id} (adoption {event.event_id[:8]}, outcome {latest[event.event_id]})"
        for event in adoptions
        if event.event_id in latest and event.event_id not in feedback
    )
    retrieval_groups: dict[str, set[tuple[str, str, str]]] = {}
    for event in events:
        if event.stage == "retrieved":
            retrieval_groups.setdefault(event.asset_id, set()).add(
                (event.session_id, event.task_key, event.query)
            )
    adopted_assets = {event.asset_id for event in adoptions}
    low_adoption = sorted(
        asset_id
        for asset_id, groups in retrieval_groups.items()
        if len(groups) >= 3 and asset_id not in adopted_assets
    )
    for label, items in (
        ("误导复核候选", misleading_assets),
        ("反复检索未采用候选（≥3 个独立检索）", low_adoption),
        ("待显式价值反馈", pending_feedback),
    ):
        if items:
            lines.append(f"{label}: {', '.join(items[:20])}")
    lines.append("使用审计只报告候选，不自动降权、修改或删除资产。")
    return "\n".join(lines)
