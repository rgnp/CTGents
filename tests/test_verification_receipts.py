"""验证回执：生成、复用、代码变化失效和 run_command 接线。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

import src.verification_receipts as receipts
from src.tools import exec as exec_mod


@pytest.fixture(autouse=True)
def _isolate_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        receipts,
        "RECEIPTS_FILE",
        tmp_path / "tasks" / "verification-receipts.jsonl",
    )


@pytest.mark.parametrize(
    "command",
    [
        "py -m pytest tests/test_tasks.py -q",
        "python -m ruff check src/tasks.py",
        "pytest -q",
        "ruff check src/",
        "git diff --check",
    ],
)
def test_verification_command_allowlist(command):
    assert receipts.is_verification_command(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "python -c \"print('x')\"",
        "git status",
        "ruff format src/",
        "pytest -q & echo unsafe",
    ],
)
def test_non_verification_command_rejected(command):
    assert receipts.is_verification_command(command) is False


def test_record_and_find_valid_receipt(tmp_path):
    saved = receipts.record_verification("pytest -q", tmp_path, 0, "12 passed")
    found = receipts.find_valid_receipt("pytest -q", tmp_path)
    assert saved is not None
    assert found == saved
    assert found.passed is True
    assert found.output_tail == "12 passed"


def test_failed_receipt_is_reusable_evidence(tmp_path):
    receipts.record_verification("ruff check src/", tmp_path, 1, "F401")
    found = receipts.find_valid_receipt("ruff check src/", tmp_path)
    assert found is not None
    assert found.passed is False
    assert found.exit_code == 1


def test_workspace_change_invalidates_receipt(tmp_path, monkeypatch):
    state = ["before"]
    monkeypatch.setattr(receipts, "workspace_fingerprint", lambda _root: state[0])
    receipts.record_verification("pytest -q", tmp_path, 0, "ok")
    state[0] = "after"
    assert receipts.find_valid_receipt("pytest -q", tmp_path) is None


def test_workspace_change_during_command_refuses_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "workspace_fingerprint", lambda _root: "after")
    saved = receipts.record_verification(
        "pytest -q",
        tmp_path,
        0,
        "passed",
        expected_fingerprint="before",
    )
    assert saved is None
    assert not receipts.RECEIPTS_FILE.exists()


def test_expired_receipt_is_ignored(tmp_path):
    receipt = receipts.record_verification("pytest -q", tmp_path, 0, "ok")
    assert receipt is not None
    old = {
        **asdict(receipt),
        "timestamp": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    }
    receipts.RECEIPTS_FILE.write_text(json.dumps(old) + "\n", encoding="utf-8")
    assert receipts.find_valid_receipt("pytest -q", tmp_path) is None


def test_corrupt_lines_do_not_hide_valid_receipt(tmp_path):
    receipt = receipts.record_verification("pytest -q", tmp_path, 0, "ok")
    assert receipt is not None
    original = receipts.RECEIPTS_FILE.read_text(encoding="utf-8")
    receipts.RECEIPTS_FILE.write_text("{broken\n" + original, encoding="utf-8")
    assert receipts.find_valid_receipt("pytest -q", tmp_path) == receipt


def test_plain_command_does_not_create_receipt(tmp_path):
    result = receipts.record_verification("git status", tmp_path, 0, "clean")
    assert result is None
    assert not receipts.RECEIPTS_FILE.exists()


def test_fallback_fingerprint_changes_with_source(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "_git_bytes", lambda *_a: None)
    src = tmp_path / "src"
    src.mkdir()
    target = src / "example.py"
    target.write_text("x = 1\n", encoding="utf-8")
    before = receipts.workspace_fingerprint(tmp_path)
    target.write_text("x = 2\n", encoding="utf-8")
    after = receipts.workspace_fingerprint(tmp_path)
    assert before != after


def test_run_command_records_real_exit_code(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, *, timeout):
            return b"3 passed", b""

    monkeypatch.setattr(exec_mod.subprocess, "Popen", lambda *_a, **_k: FakeProcess())
    monkeypatch.setattr(exec_mod, "_verification_fingerprint", lambda _command: "before")
    monkeypatch.setattr(
        receipts,
        "record_verification",
        lambda command, workdir, exit_code, output, **kwargs: captured.update(
            command=command,
            workdir=workdir,
            exit_code=exit_code,
            output=output,
            expected_fingerprint=kwargs.get("expected_fingerprint"),
        ),
    )
    result = exec_mod.run_command("pytest -q", workdir=str(tmp_path))
    assert result == "3 passed"
    assert captured["command"] == "pytest -q"
    assert captured["exit_code"] == 0
    assert captured["output"] == "3 passed"
    assert captured["expected_fingerprint"]
