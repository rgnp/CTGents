"""Shared work receipts keep identity, evidence, versions, and handoff decisions."""

from __future__ import annotations

import hashlib

import pytest

from src import work_receipts as receipts


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(receipts, "WORK_RECEIPTS_FILE", tmp_path / "tasks" / "work-receipts.jsonl")
    return receipts


def test_receipt_versions_project_local_artifacts(isolated, tmp_path):
    artifact = tmp_path / "docs" / "result.md"
    artifact.parent.mkdir()
    artifact.write_text("version one", encoding="utf-8")

    receipt = isolated.record_work_receipt(
        "task",
        "task-1",
        "completed",
        evidence="tests passed",
        artifact_paths=("docs/result.md", tmp_path.parent / "outside.md"),
    )

    assert receipt is not None
    assert receipt.evidence_sha256 == hashlib.sha256(b"tests passed").hexdigest()
    assert len(receipt.artifacts) == 1
    assert receipt.artifacts[0].path == "docs/result.md"
    assert receipt.artifacts[0].sha256 == hashlib.sha256(b"version one").hexdigest()


def test_exact_receipt_is_idempotent(isolated):
    first = isolated.record_work_receipt("task", "task-1", "failed", evidence="same")
    second = isolated.record_work_receipt("task", "task-1", "failed", evidence="same")
    assert first == second
    assert len(isolated._read_receipts()) == 1


def test_caller_key_separates_real_occurrences_but_deduplicates_retry(isolated):
    first = isolated.record_work_receipt(
        "heartbeat",
        "work-1",
        "failed",
        evidence="same",
        idempotency_key="run-1",
    )
    retry = isolated.record_work_receipt(
        "heartbeat",
        "work-1",
        "failed",
        evidence="same",
        idempotency_key="run-1",
    )
    second_run = isolated.record_work_receipt(
        "heartbeat",
        "work-1",
        "failed",
        evidence="same",
        idempotency_key="run-2",
    )
    assert first == retry
    assert second_run != first
    assert len(isolated._read_receipts()) == 2


def test_delivery_requires_explicit_disposition(isolated):
    delivery = isolated.record_work_receipt(
        "heartbeat", "digest-1", "delivered", evidence="summary"
    )
    assert delivery is not None
    assert isolated.latest_pending_delivery() == delivery

    result = isolated.resolve_latest_delivery("revise", "补充原文证据")

    assert result.startswith("✅")
    assert isolated.latest_pending_delivery() is None
    disposition = isolated._read_receipts()[-1]
    assert disposition.stage == "revision_requested"
    assert disposition.parent_id == delivery.receipt_id


def test_delivery_links_only_undelivered_runs(isolated):
    first = isolated.record_work_receipt("heartbeat", "work-1", "completed", evidence="one")
    assert first is not None
    assert isolated.undelivered_work_links() == (f"work-receipt:{first.receipt_id}",)
    isolated.record_work_receipt("heartbeat", "digest-1", "delivered", evidence="batch")
    assert isolated.undelivered_work_links() == ()
    second = isolated.record_work_receipt("heartbeat", "work-2", "failed", evidence="two")
    assert second is not None
    assert isolated.undelivered_work_links() == (f"work-receipt:{second.receipt_id}",)


def test_status_reports_versions_pending_and_gap_links(isolated, tmp_path):
    artifact = tmp_path / "result.txt"
    artifact.write_text("result", encoding="utf-8")
    isolated.record_work_receipt(
        "task",
        "task-1",
        "completed",
        artifact_paths=("result.txt",),
        links=("gap:abc123def456",),
    )
    isolated.record_work_receipt("heartbeat", "digest-1", "delivered", evidence="summary")

    status = isolated.format_work_status()

    assert "版本化产物 1 个" in status
    assert "待用户处置 1 个" in status
    assert "Gap 1 个" in status


def test_artifact_drift_compares_only_latest_version(isolated, tmp_path):
    artifact = tmp_path / "result.txt"
    artifact.write_text("v1", encoding="utf-8")
    isolated.record_work_receipt(
        "task", "task-1", "completed", artifact_paths=("result.txt",)
    )
    assert isolated.artifact_drift() == ((), ())

    artifact.write_text("v2", encoding="utf-8")
    assert isolated.artifact_drift() == ((), ("result.txt",))
    isolated.record_work_receipt(
        "task", "task-2", "completed", artifact_paths=("result.txt",)
    )
    assert isolated.artifact_drift() == ((), ())

    artifact.unlink()
    assert isolated.artifact_drift() == (("result.txt",), ())


def test_corrupt_lines_do_not_hide_valid_receipts(isolated):
    isolated.record_work_receipt("task", "task-1", "failed", evidence="failure")
    original = isolated.WORK_RECEIPTS_FILE.read_text(encoding="utf-8")
    isolated.WORK_RECEIPTS_FILE.write_text("{broken\n" + original, encoding="utf-8")
    assert len(isolated._read_receipts()) == 1


def test_invalid_source_stage_or_empty_identity_is_rejected(isolated):
    assert isolated.record_work_receipt("gap", "x", "completed") is None
    assert isolated.record_work_receipt("task", "x", "unknown") is None
    assert isolated.record_work_receipt("task", "", "completed") is None
