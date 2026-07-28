"""Cognitive asset evidence: retrieved -> explicitly adopted -> deterministic task outcome."""

from __future__ import annotations

import json

import pytest

from src import asset_usage

_REAL_CURRENT_TASK_KEY = asset_usage.current_task_key


@pytest.fixture(autouse=True)
def usage(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_usage, "USAGE_FILE", tmp_path / "asset-usage.jsonl")
    monkeypatch.setattr(asset_usage, "_current_session_id", lambda: "session-1")
    monkeypatch.setattr(asset_usage, "current_task_key", lambda text=None: "task-1")
    return asset_usage


def test_retrieval_then_adoption_is_auditable(usage):
    usage.record_retrieval("memory", ["rule-a"], "怎么处理")
    result = usage.adopt_asset("memory", "rule-a", "用于选择回滚策略")

    events = usage._read_events()
    assert result.startswith("✅")
    assert [event.stage for event in events] == ["retrieved", "adopted"]
    assert events[-1].evidence == f"retrieval:{events[0].event_id}"


def test_adoption_requires_same_session_retrieval(usage, monkeypatch):
    usage.record_retrieval("knowledge", ["knowledge:a.md"], "topic")
    monkeypatch.setattr(usage, "_current_session_id", lambda: "session-2")
    result = usage.adopt_asset("knowledge", "knowledge:a.md", "作为事实依据")
    assert result.startswith("❌")


def test_duplicate_adoption_is_idempotent(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "first purpose")
    usage.adopt_asset("memory", "rule-a", "second purpose")
    assert sum(event.stage == "adopted" for event in usage._read_events()) == 1


def test_task_outcome_updates_latest_result(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "guide implementation")
    assert usage.record_task_outcome("failed", "tests failed") == 1
    assert usage.record_task_outcome("passed", "tests passed") == 1
    assert usage.record_task_outcome("passed", "tests passed again") == 0

    summary = usage.format_usage_summary("memory")
    assert "参与通过任务 1 次" in summary
    assert "失败 0 次" in summary
    assert "待结果 0 次" in summary


def test_terminal_outcome_is_not_reattributed_by_same_task_title(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "guide first task")
    assert usage.record_task_outcome("passed", "first task passed") == 1
    assert usage.record_task_outcome("failed", "later same-title task failed") == 0

    usage.record_retrieval("memory", ["rule-a"], "query again")
    usage.adopt_asset("memory", "rule-a", "guide later task")
    adoptions = [event for event in usage._read_events() if event.stage == "adopted"]
    assert len(adoptions) == 2


def test_feedback_requires_adoption_with_task_outcome(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "guide task")

    result = usage.feedback_asset(
        "memory", "rule-a", "helpful", "prevented a repeated mistake"
    )

    assert result.startswith("❌")
    assert not any(event.stage == "feedback" for event in usage._read_events())


def test_explicit_feedback_is_auditable_and_idempotent(usage):
    usage.record_retrieval("knowledge", ["notes/a.md"], "query")
    usage.adopt_asset("knowledge", "notes/a.md", "support design")
    usage.record_task_outcome("passed", "tests passed")

    first = usage.feedback_asset(
        "knowledge", "notes/a.md", "helpful", "supplied the decisive constraint"
    )
    second = usage.feedback_asset(
        "knowledge", "notes/a.md", "helpful", "same judgment again"
    )

    events = usage._read_events()
    outcome = next(event for event in events if event.stage == "outcome")
    feedback = [event for event in events if event.stage == "feedback"]
    assert first.startswith("✅")
    assert second.startswith("资产已有相同反馈")
    assert len(feedback) == 1
    assert feedback[0].evidence == f"outcome:{outcome.event_id}"


def test_feedback_can_be_explicitly_corrected(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "guide task")
    usage.record_task_outcome("failed", "acceptance failed")
    usage.feedback_asset("memory", "rule-a", "misleading", "pointed at the wrong fix")
    usage.feedback_asset("memory", "rule-a", "helpful", "later review found it correct")

    summary = usage.format_usage_summary("memory")
    assert "helpful 1 次" in summary
    assert "misleading 0 次" in summary
    assert "误导复核候选" not in summary


def test_feedback_can_target_adoption_by_unique_prefix(usage, monkeypatch):
    usage.record_retrieval("memory", ["rule-a"], "first")
    usage.adopt_asset("memory", "rule-a", "first task")
    usage.record_task_outcome("passed", "first passed")
    first_adoption = next(
        event for event in usage._read_events() if event.stage == "adopted"
    )
    monkeypatch.setattr(usage, "current_task_key", lambda text=None: "task-2")
    usage.record_retrieval("memory", ["rule-a"], "second")
    usage.adopt_asset("memory", "rule-a", "second task")
    usage.record_task_outcome("passed", "second passed")

    result = usage.feedback_asset(
        "memory",
        "rule-a",
        "misleading",
        "the first use chose the wrong branch",
        first_adoption.event_id[:8],
    )

    feedback = next(
        event for event in usage._read_events() if event.stage == "feedback"
    )
    assert result.startswith("✅")
    assert feedback.adoption_id == first_adoption.event_id


def test_usage_summary_reports_only_read_only_candidates(usage):
    for index in range(3):
        usage.record_retrieval("memory", ["never-used"], f"query-{index}")
    usage.record_retrieval("memory", ["bad-rule"], "bad query")
    usage.adopt_asset("memory", "bad-rule", "guide task")
    usage.record_task_outcome("failed", "failed")
    usage.feedback_asset("memory", "bad-rule", "misleading", "caused wrong choice")
    usage.record_retrieval("memory", ["unrated-rule"], "other query")
    usage.adopt_asset("memory", "unrated-rule", "guide another choice")
    usage.record_task_outcome("passed", "passed")

    summary = usage.format_usage_summary("memory")

    assert "误导复核候选: bad-rule" in summary
    assert "反复检索未采用候选（≥3 个独立检索）: never-used" in summary
    assert "待显式价值反馈: unrated-rule (adoption " in summary
    assert "outcome passed" in summary
    assert "不自动降权、修改或删除资产" in summary


def test_outcome_ignores_adoption_from_other_task(usage, monkeypatch):
    usage.record_retrieval("memory", ["rule-a"], "query")
    usage.adopt_asset("memory", "rule-a", "guide task one")
    monkeypatch.setattr(usage, "current_task_key", lambda text=None: "task-2")
    assert usage.record_task_outcome("passed", "done") == 0


def test_corrupt_event_lines_do_not_hide_valid_events(usage):
    usage.record_retrieval("memory", ["rule-a"], "query")
    original = usage.USAGE_FILE.read_text(encoding="utf-8")
    usage.USAGE_FILE.write_text("{broken\n" + original, encoding="utf-8")
    assert len(usage._read_events()) == 1


def test_unknown_kind_is_rejected(usage):
    assert usage.adopt_asset("session", "x", "purpose").startswith("❌")


def test_current_task_key_stable_across_progress_edits():
    before = "# 当前任务：闭环\n\n- [ ] 第一步"
    after = "# 当前任务：闭环\n\n- [x] 第一步\n- [ ] 第二步"
    assert _REAL_CURRENT_TASK_KEY(before) == _REAL_CURRENT_TASK_KEY(after)


def test_event_schema_is_json_serializable(usage):
    usage.record_retrieval("knowledge", ["knowledge:a.md"], "query")
    payload = json.loads(usage.USAGE_FILE.read_text(encoding="utf-8").splitlines()[0])
    assert payload["stage"] == "retrieved"
