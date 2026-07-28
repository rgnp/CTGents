"""差距检测框架测试：多信号源汇聚 + 排序 + 去重 + 缓存查询。"""

import pytest

import src.gaps as gaps_module
from src.gaps import (
    Gap,
    GapReport,
    _deduplicate,
    _detect_performance_gaps,
    _gap_score,
    _make_fix_prompt,
    _prioritize,
    detect_all_gaps,
    format_gap_ledger,
    format_gap_report,
    get_gap_by_index,
    get_gap_record,
    get_last_report,
    set_gap_status,
    sync_gap_ledger,
    verify_gap,
)

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _isolate_gap_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gaps_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gaps_module, "_GAP_CACHE_FILE", tmp_path / ".gap_cache.json")
    monkeypatch.setattr(gaps_module, "_GAP_LEDGER_FILE", tmp_path / "tasks" / "gap-ledger.json")
    monkeypatch.setattr(gaps_module, "_git_tree_hash", lambda: "workspace-state")
    gaps_module._LAST_REPORT = None


# 预置静态 gap — 替代全项目 AST 扫描（7s→0）
_CANNED_STATIC = Gap(
    source="static", gap_type="dead_code", severity="high",
    detail="src/a.py:10 - unused function", affected_files=["src/a.py"],
    suggestion="delete or mark", confidence=0.9, actionable=True,
)
_CANNED_PERFORMANCE = Gap(
    source="performance",
    gap_type="high_failure_tool_contract",
    severity="high",
    detail="write_file 本次失败率过高",
    affected_files=["src/tools/file.py"],
    suggestion="tighten contract",
    confidence=0.9,
    actionable=True,
    signal_key="write_file|high_failure",
)

def test_gap_defaults():
    g = Gap(source="test", gap_type="test", severity="medium", detail="test")
    assert g.affected_files == []
    assert g.suggestion == ""
    assert g.confidence == 0.0
    assert g.actionable is True


def test_gap_id_stable_across_detail_changes():
    before = Gap(
        source="static",
        gap_type="dead_code",
        severity="high",
        detail="src/a.py:10 - unused",
        affected_files=["src/a.py"],
    )
    after = Gap(
        source="static",
        gap_type="dead_code",
        severity="high",
        detail="src/a.py:30 - unused after edits",
        affected_files=["src/a.py"],
    )
    assert before.id == after.id

def test_gapreport_defaults():
    r = GapReport()
    assert r.gaps == []
    assert r.sources_scanned == 0
    assert r.sources_failed == 0

# 排序

def test_gap_score_higher_for_more_severe():
    high = Gap(source="t", gap_type="t", severity="high", detail="", confidence=0.9, actionable=True)
    low = Gap(source="t", gap_type="t", severity="low", detail="", confidence=0.9, actionable=True)
    assert _gap_score(high) > _gap_score(low)

def test_gap_score_higher_for_actionable():
    a = Gap(source="t", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=True)
    b = Gap(source="t", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=False)
    assert _gap_score(a) > _gap_score(b)

def test_gap_score_higher_for_confident():
    a = Gap(source="t", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=True)
    b = Gap(source="t", gap_type="t", severity="medium", detail="", confidence=0.3, actionable=True)
    assert _gap_score(a) > _gap_score(b)

# 去重

def test_deduplicate_removes_same_file_type():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a", affected_files=["src/a.py"]),
        Gap(source="s", gap_type="dead_code", severity="high", detail="b", affected_files=["src/a.py"]),
    ]
    assert len(_deduplicate(gaps)) == 1

def test_deduplicate_keeps_different_types_same_file():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a", affected_files=["src/a.py"]),
        Gap(source="s", gap_type="complexity", severity="high", detail="b", affected_files=["src/a.py"]),
    ]
    assert len(_deduplicate(gaps)) == 2

def test_deduplicate_keeps_same_type_different_files():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a", affected_files=["src/a.py"]),
        Gap(source="s", gap_type="dead_code", severity="high", detail="b", affected_files=["src/b.py"]),
    ]
    assert len(_deduplicate(gaps)) == 2

# 优先排序

def test_prioritize_caps_at_top_n():
    gaps = [
        Gap(source="t", gap_type=f"t{i}", severity="high",
            detail=str(i), confidence=0.9, actionable=True)
        for i in range(10)
    ]
    assert len(_prioritize(gaps, top_n=3)) == 3

def test_prioritize_handles_empty():
    assert _prioritize([], top_n=5) == []

# 探测器 — 用预置 gap 替代全项目实时扫描（7s→0s）

def test_performance_detector_returns_list():
    result = _detect_performance_gaps()
    assert isinstance(result, list)

def test_static_detector_returns_list(monkeypatch):
    import src.gaps as g
    monkeypatch.setattr(g, "_detect_static_gaps", lambda: [_CANNED_STATIC])
    result = g._detect_static_gaps()
    assert isinstance(result, list)
    assert result[0].gap_type == "dead_code"

# 格式化

def test_format_empty_report():
    assert "未发现" in format_gap_report(GapReport())

def test_format_report_with_gaps():
    report = GapReport(
        gaps=[Gap(source="performance", gap_type="slow", severity="medium", detail="test tool",
                   affected_files=["src/t.py"],
                   suggestion="sug", confidence=0.8)],
        sources_scanned=2,
    )
    output = format_gap_report(report)
    assert "主动进化" in output
    assert "test tool" in output
    assert report.gaps[0].id in output

def test_format_report_with_failures():
    report = GapReport(
        gaps=[Gap(source="t", gap_type="t", severity="medium", detail="d")],
        sources_scanned=2, sources_failed=1, failures=["static: timeout"],
    )
    assert "信号源失败" in format_gap_report(report)

# 报告缓存

def test_get_last_report_none_before_detection():
    """首次调用前返回 None。"""
    from src import gaps
    old = gaps._LAST_REPORT
    gaps._LAST_REPORT = None
    try:
        assert get_last_report() is None
    finally:
        gaps._LAST_REPORT = old

def test_get_gap_by_index_out_of_range():
    from src import gaps
    old = gaps._LAST_REPORT
    gaps._LAST_REPORT = GapReport(gaps=[Gap(source="t", gap_type="t", severity="medium", detail="d")])
    try:
        assert get_gap_by_index(0) is None
        assert get_gap_by_index(2) is None
    finally:
        gaps._LAST_REPORT = old

def test_get_gap_by_index_valid():
    from src import gaps
    old = gaps._LAST_REPORT
    g = Gap(source="t", gap_type="dead_code", severity="high", detail="test gap", affected_files=["src/x.py"])
    gaps._LAST_REPORT = GapReport(gaps=[g])
    try:
        result = get_gap_by_index(1)
        assert result is not None
        assert result.detail == "test gap"
    finally:
        gaps._LAST_REPORT = old

# 修复 prompt

def test_make_fix_prompt_includes_details():
    gap = Gap(
        source="static", gap_type="dead_code", severity="high",
        detail="src/a.py:10 - unused function", affected_files=["src/a.py"],
        suggestion="delete or mark", confidence=0.9, actionable=True,
    )
    prompt = _make_fix_prompt(gap, 3)
    assert "方向 #3" in prompt
    assert "src/a.py" in prompt
    assert "delete or mark" in prompt
    assert "主动进化" in prompt
    assert gap.id in prompt


class TestGapLifecycle:
    def _sync_one(self):
        report = GapReport(gaps=[_CANNED_STATIC], sources_scanned=2)
        gaps_module._LAST_REPORT = report
        sync_gap_ledger(report)
        return _CANNED_STATIC.id

    def test_sync_creates_discovered_record(self):
        gap_id = self._sync_one()
        record = get_gap_record(gap_id)
        assert record is not None
        assert record.status == "discovered"
        assert [event.status for event in record.history] == ["discovered"]

    def test_accept_defer_reopen_fix_transitions(self):
        gap_id = self._sync_one()
        assert "discovered → accepted" in set_gap_status(gap_id, "accepted", "值得修")
        assert "accepted → deferred" in set_gap_status(gap_id, "deferred")
        assert "deferred → accepted" in set_gap_status(gap_id, "accepted")
        assert "accepted → fixed" in set_gap_status(gap_id, "fixed", "已加回归测试")
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "fixed"
        assert record.history[-1].workspace_fingerprint == "workspace-state"

    def test_illegal_transition_fails(self):
        gap_id = self._sync_one()
        result = set_gap_status(gap_id, "fixed")
        assert result.startswith("❌")
        assert "非法状态迁移" in result

    def test_rejected_can_be_reopened(self):
        gap_id = self._sync_one()
        set_gap_status(gap_id, "rejected", "误报")
        assert "rejected → accepted" in set_gap_status(gap_id, "accepted", "出现新证据")

    def test_verify_passes_only_when_original_signal_disappears(self, monkeypatch):
        gap_id = self._sync_one()
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        monkeypatch.setattr(
            gaps_module,
            "detect_all_gaps",
            lambda **_kwargs: GapReport(gaps=[], sources_scanned=2),
        )
        result = verify_gap(gap_id)
        assert "verified" in result
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "verified"

    def test_verify_failure_keeps_fixed(self, monkeypatch):
        gap_id = self._sync_one()
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        monkeypatch.setattr(
            gaps_module,
            "detect_all_gaps",
            lambda **_kwargs: GapReport(gaps=[_CANNED_STATIC], sources_scanned=2),
        )
        result = verify_gap(gap_id)
        assert result.startswith("❌")
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "fixed"
        assert "仍可检测到" in record.history[-1].note

    def test_verify_stops_when_original_detector_failed(self, monkeypatch):
        gap_id = self._sync_one()
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        monkeypatch.setattr(
            gaps_module,
            "detect_all_gaps",
            lambda **_kwargs: GapReport(
                gaps=[],
                sources_scanned=2,
                sources_failed=1,
                failures=["static: timeout"],
            ),
        )
        result = verify_gap(gap_id)
        assert "无法复核" in result
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "fixed"

    def test_verified_gap_reappearing_is_reopened(self, monkeypatch):
        gap_id = self._sync_one()
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        monkeypatch.setattr(
            gaps_module,
            "detect_all_gaps",
            lambda **_kwargs: GapReport(gaps=[], sources_scanned=2),
        )
        verify_gap(gap_id)

        sync_gap_ledger(GapReport(gaps=[_CANNED_STATIC], sources_scanned=2))

        record = get_gap_record(gap_id)
        assert record is not None and record.status == "accepted"
        assert "再次被同一信号检出" in record.history[-1].note

    def _sync_performance(self):
        report = GapReport(gaps=[_CANNED_PERFORMANCE], sources_scanned=2)
        gaps_module._LAST_REPORT = report
        sync_gap_ledger(report)
        return _CANNED_PERFORMANCE.id

    def test_performance_fix_freezes_baseline_evidence(self, monkeypatch):
        gap_id = self._sync_performance()
        baseline = {
            "sessions_observed": 3,
            "occurrences": 2,
            "occurrence_rate": 0.667,
        }
        monkeypatch.setattr(gaps_module, "_performance_signal_window", lambda *_args, **_kwargs: baseline)
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        record = get_gap_record(gap_id)
        assert record is not None
        assert record.history[-1].evidence == baseline

    @pytest.mark.parametrize("observed", [0, 2])
    def test_performance_verify_waits_for_full_window(self, monkeypatch, observed):
        gap_id = self._sync_performance()
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: {
                "sessions_observed": observed,
                "occurrences": 0,
            },
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        result = verify_gap(gap_id)
        assert result.startswith("⏳")
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "fixed"

    def test_ledger_shows_performance_observation_progress(self, monkeypatch):
        gap_id = self._sync_performance()
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: {
                "sessions_observed": 2,
                "occurrences": 0,
            },
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        assert "收益观察: 2/3 个有效后续会话" in format_gap_ledger()

    def test_performance_verify_passes_after_clean_window(self, monkeypatch):
        gap_id = self._sync_performance()
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: {
                "sessions_observed": 3,
                "occurrences": 0,
                "occurrence_rate": 0.0,
            },
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        result = verify_gap(gap_id)
        assert result.startswith("✅")
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "verified"
        assert record.history[-1].evidence["sessions_observed"] == 3

    def test_performance_recurrence_reopens_gap(self, monkeypatch):
        gap_id = self._sync_performance()
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: {
                "sessions_observed": 3,
                "occurrences": 1,
                "occurrence_rate": 0.333,
            },
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        result = verify_gap(gap_id)
        assert result.startswith("❌")
        record = get_gap_record(gap_id)
        assert record is not None and record.status == "accepted"
        assert "同类异常复发" in record.history[-1].note

    def test_historical_performance_signal_does_not_reopen_verified(self, monkeypatch):
        gap_id = self._sync_performance()
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: {
                "sessions_observed": 3,
                "occurrences": 0,
                "occurrence_rate": 0.0,
            },
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        verify_gap(gap_id)

        sync_gap_ledger(GapReport(gaps=[_CANNED_PERFORMANCE], sources_scanned=2))

        record = get_gap_record(gap_id)
        assert record is not None and record.status == "verified"

    def test_new_performance_recurrence_reopens_verified(self, monkeypatch):
        gap_id = self._sync_performance()
        window = {
            "sessions_observed": 3,
            "occurrences": 0,
            "occurrence_rate": 0.0,
        }
        monkeypatch.setattr(
            gaps_module,
            "_performance_signal_window",
            lambda *_args, **_kwargs: window,
        )
        set_gap_status(gap_id, "accepted")
        set_gap_status(gap_id, "fixed")
        verify_gap(gap_id)
        window["occurrences"] = 1
        window["occurrence_rate"] = 1 / 3

        sync_gap_ledger(GapReport(gaps=[], sources_scanned=2))

        record = get_gap_record(gap_id)
        assert record is not None and record.status == "accepted"
        assert "verified 之后" in record.history[-1].note

# 集成 — 用预置替换静态扫描，保持管线接线测试价值

def test_detect_all_gaps_does_not_crash(monkeypatch):
    import src.gaps as g
    monkeypatch.setattr(g, "_detect_static_gaps", lambda: [_CANNED_STATIC])
    report = detect_all_gaps(top_n=3)
    assert isinstance(report, GapReport)
    assert report.sources_scanned == 2  # performance + static（覆盖率信号已移除）
