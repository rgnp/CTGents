"""集成测试 tracker.py — 确认工具调用感知层端到端工作。"""

import pytest

from src.tracker import (
    detect_anomalies,
    flush,
    get_cross_session_baseline,
    get_session_aggregates,
    record_tool_call,
    set_session,
)


@pytest.fixture(autouse=True)
def _clean_tracker():
    """每个测试前清空状态。"""
    set_session(None)
    yield
    set_session(None)


class TestRecordAndFlush:
    def test_record_and_read_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        sid = "test-session-1"
        set_session(sid)

        record_tool_call("read_file", 12.3, True)
        record_tool_call("write_file", 45.6, False, error="ValueError")
        record_tool_call("read_file", 8.1, True)
        flush()

        agg = get_session_aggregates(sid)
        assert agg["total_calls"] == 3
        assert agg["total_failures"] == 1
        assert agg["tools"]["read_file"]["count"] == 2
        assert agg["tools"]["read_file"]["failures"] == 0
        assert agg["tools"]["write_file"]["count"] == 1
        assert agg["tools"]["write_file"]["failures"] == 1

    def test_empty_session(self):
        agg = get_session_aggregates("nonexistent-session")
        assert agg["total_calls"] == 0

    def test_no_session_skips_recording(self):
        set_session(None)
        record_tool_call("read_file", 1.0, True)


class TestBaseline:
    def test_baseline_from_multiple_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        for sid, durations in [("s1", [10, 20, 30]), ("s2", [15, 25, 100])]:
            set_session(sid)
            for d in durations:
                record_tool_call("grep_code", d, True)
            flush()

        baseline = get_cross_session_baseline(recent_n=3)
        assert baseline["sessions_analyzed"] >= 2
        assert baseline["total_calls"] >= 6
        g = baseline["tools"]["grep_code"]
        assert g["avg_ms"] > 0
        assert g["p50_ms"] > 0

    def test_baseline_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        baseline = get_cross_session_baseline(recent_n=1)
        assert baseline["sessions_analyzed"] == 0


class TestAnomalyDetection:
    def test_no_anomalies_when_few_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        sid = "few-calls"
        set_session(sid)
        for _ in range(3):
            record_tool_call("read_file", 10, True)
        flush()
        assert detect_anomalies(sid) == []

    def test_high_failure_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        sid = "many-failures"
        set_session(sid)
        for _ in range(8):
            record_tool_call("write_file", 50, False, error="OSError")
        for _ in range(2):
            record_tool_call("write_file", 50, True)
        flush()
        anomalies = detect_anomalies(sid)
        failure = [a for a in anomalies if a["type"] == "high_failure"]
        assert len(failure) > 0
        assert failure[0]["tool"] == "write_file"
