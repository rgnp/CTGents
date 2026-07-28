"""Shared immutable receipts for work identity, evidence, and artifact versions."""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .paths import CORE_ROOT, TASKS_DIR, display_path, resolve_runtime_path

PROJECT_ROOT = CORE_ROOT
WORK_RECEIPTS_FILE = TASKS_DIR / "work-receipts.jsonl"
MAX_RECEIPTS = 1000
_SOURCES = frozenset({"task", "heartbeat"})
_STAGES = frozenset(
    {
        "completed",
        "failed",
        "abandoned",
        "delivered",
        "accepted",
        "rejected",
        "revision_requested",
    }
)
_lock = threading.Lock()


@dataclass(frozen=True)
class ArtifactVersion:
    """Content identity for one project-local artifact."""

    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class WorkReceipt:
    """One append-only lifecycle fact; subsystem state remains authoritative elsewhere."""

    receipt_id: str
    work_id: str
    source: str
    stage: str
    timestamp: str
    goal: str
    evidence: str
    evidence_sha256: str
    workspace_fingerprint: str
    artifacts: tuple[ArtifactVersion, ...]
    parent_id: str = ""
    links: tuple[str, ...] = ()
    idempotency_key: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def derive_work_id(source: str, seed: str) -> str:
    """Derive a stable ID from the owning subsystem and its stable work description."""
    normalized = " ".join(seed.strip().lower().split())
    if not normalized:
        return ""
    return hashlib.sha256(f"{source}:{normalized}".encode()).hexdigest()[:12]


def _artifact_version(path: Path | str) -> ArtifactVersion | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = resolve_runtime_path(candidate, PROJECT_ROOT)
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    try:
        payload = resolved.read_bytes()
    except OSError:
        return None
    try:
        stored_path = resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        stored_path = display_path(resolved)
    return ArtifactVersion(
        path=stored_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def _read_receipts() -> list[WorkReceipt]:
    if not WORK_RECEIPTS_FILE.exists():
        return []
    try:
        lines = WORK_RECEIPTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    receipts: list[WorkReceipt] = []
    for line in lines:
        try:
            raw = json.loads(line)
            raw["artifacts"] = tuple(
                ArtifactVersion(**artifact) for artifact in raw.get("artifacts", [])
            )
            raw["links"] = tuple(raw.get("links", []))
            receipts.append(WorkReceipt(**raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return receipts


def _write_receipts(receipts: list[WorkReceipt]) -> bool:
    try:
        WORK_RECEIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = WORK_RECEIPTS_FILE.with_suffix(".tmp")
        temp.write_text(
            "".join(json.dumps(asdict(item), ensure_ascii=False) + "\n" for item in receipts),
            encoding="utf-8",
        )
        temp.replace(WORK_RECEIPTS_FILE)
    except OSError:
        return False
    return True


def record_work_receipt(
    source: str,
    work_id: str,
    stage: str,
    *,
    goal: str = "",
    evidence: str = "",
    artifact_paths: tuple[Path | str, ...] = (),
    capture_workspace: bool = False,
    parent_id: str = "",
    links: tuple[str, ...] = (),
    idempotency_key: str = "",
) -> WorkReceipt | None:
    """Append one idempotent receipt without replacing subsystem-owned state."""
    owner = source.strip().lower()
    status = stage.strip().lower()
    identity = work_id.strip()
    if owner not in _SOURCES or status not in _STAGES or not identity:
        return None
    evidence_text = evidence.strip()
    versions = tuple(
        version
        for version in (_artifact_version(path) for path in dict.fromkeys(artifact_paths))
        if version is not None
    )
    fingerprint = ""
    if capture_workspace:
        with contextlib.suppress(Exception):
            from .verification_receipts import workspace_fingerprint

            fingerprint = workspace_fingerprint(PROJECT_ROOT)
    evidence_hash = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
    normalized_links = tuple(dict.fromkeys(link.strip() for link in links if link.strip()))
    caller_key = idempotency_key.strip()
    with _lock:
        receipts = _read_receipts()
        existing = next(
            (
                item
                for item in reversed(receipts)
                if item.source == owner
                and (
                    (caller_key and item.idempotency_key == caller_key)
                    or (
                        not caller_key
                        and not item.idempotency_key
                        and item.work_id == identity
                        and item.stage == status
                        and item.evidence_sha256 == evidence_hash
                        and item.artifacts == versions
                        and item.parent_id == parent_id
                        and item.links == normalized_links
                    )
                )
            ),
            None,
        )
        if existing is not None:
            return existing
        receipt = WorkReceipt(
            receipt_id=uuid.uuid4().hex,
            work_id=identity,
            source=owner,
            stage=status,
            timestamp=_now(),
            goal=goal.strip()[:500],
            evidence=evidence_text[-2000:],
            evidence_sha256=evidence_hash,
            workspace_fingerprint=fingerprint,
            artifacts=versions,
            parent_id=parent_id.strip(),
            links=normalized_links,
            idempotency_key=caller_key,
        )
        if not _write_receipts((receipts + [receipt])[-MAX_RECEIPTS:]):
            return None
    return receipt


def latest_pending_delivery(source: str = "heartbeat") -> WorkReceipt | None:
    """Return the newest delivered receipt that has no explicit user disposition."""
    owner = source.strip().lower()
    receipts = _read_receipts()
    resolved = {
        item.parent_id
        for item in receipts
        if item.stage in {"accepted", "rejected", "revision_requested"} and item.parent_id
    }
    return next(
        (
            item
            for item in reversed(receipts)
            if item.source == owner and item.stage == "delivered" and item.receipt_id not in resolved
        ),
        None,
    )


def undelivered_work_links(source: str = "heartbeat") -> tuple[str, ...]:
    """Return source work receipts created after the newest delivery."""
    owner = source.strip().lower()
    receipts = _read_receipts()
    last_delivery = max(
        (
            index
            for index, item in enumerate(receipts)
            if item.source == owner and item.stage == "delivered"
        ),
        default=-1,
    )
    return tuple(
        f"work-receipt:{item.receipt_id}"
        for item in receipts[last_delivery + 1 :]
        if item.source == owner and item.stage in {"completed", "failed"}
    )


def resolve_latest_delivery(disposition: str, note: str = "") -> str:
    """Record accept/reject/revision feedback for the latest heartbeat delivery."""
    mapping = {
        "accept": "accepted",
        "reject": "rejected",
        "revise": "revision_requested",
    }
    stage = mapping.get(disposition.strip().lower())
    if stage is None:
        return f"❌ 未知交还处置：{disposition}"
    delivery = latest_pending_delivery()
    if delivery is None:
        return "没有待处置的 Heartbeat 交还。"
    receipt = record_work_receipt(
        "heartbeat",
        delivery.work_id,
        stage,
        goal=delivery.goal,
        evidence=note.strip() or f"用户选择 {disposition}",
        parent_id=delivery.receipt_id,
        links=(f"delivery:{delivery.receipt_id}",),
    )
    if receipt is None:
        return "❌ Heartbeat 交还处置未能写入回执。"
    labels = {
        "accepted": "接受",
        "rejected": "拒绝",
        "revision_requested": "要求修订",
    }
    return f"✅ 已{labels[stage]} Heartbeat 交还 {delivery.work_id}。"


def artifact_drift() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return missing and changed paths against each artifact's newest receipt version."""
    latest: dict[str, ArtifactVersion] = {}
    for receipt in _read_receipts():
        for artifact in receipt.artifacts:
            latest[artifact.path] = artifact
    missing: list[str] = []
    changed: list[str] = []
    for path, expected in latest.items():
        current = _artifact_version(path)
        if current is None:
            missing.append(path)
        elif current.sha256 != expected.sha256:
            changed.append(path)
    return tuple(sorted(missing)), tuple(sorted(changed))


def format_work_status() -> str:
    """Render the shared nervous-system view without copying owned subsystem state."""
    receipts = _read_receipts()
    if not receipts:
        return "共享工作回执：暂无。"
    pending = sum(
        1
        for item in receipts
        if item.stage == "delivered"
        and not any(
            later.parent_id == item.receipt_id
            and later.stage in {"accepted", "rejected", "revision_requested"}
            for later in receipts
        )
    )
    artifacts = {
        (artifact.path, artifact.sha256)
        for item in receipts
        for artifact in item.artifacts
    }
    task_ids = {item.work_id for item in receipts if item.source == "task"}
    linked_assets = 0
    with contextlib.suppress(Exception):
        from .asset_usage import _read_events

        linked_assets = len(
            {
                (event.asset_kind, event.asset_id)
                for event in _read_events()
                if event.task_key in task_ids and event.stage == "adopted"
            }
        )
    linked_gaps = {
        link.removeprefix("gap:")
        for item in receipts
        for link in item.links
        if link.startswith("gap:")
    }
    latest = receipts[-1]
    missing, changed = artifact_drift()
    return (
        "共享工作回执："
        f"{len(receipts)} 条，版本化产物 {len(artifacts)} 个，"
        f"待用户处置 {pending} 个，"
        f"当前产物漂移 {len(changed)} 个/缺失 {len(missing)} 个；"
        f"关联认知资产 {linked_assets} 项，Gap {len(linked_gaps)} 个。\n"
        f"最近：{latest.source}:{latest.work_id} → {latest.stage}。"
    )
