"""集成测试 tracker.py — 确认工具调用感知层端到端工作。"""

import pytest

from src.tracker import (
    _discover_sessions,
    _read_session,
    detect_anomalies,
    flush,
    get_anomaly_signal_window,
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

    def test_slow_detected(self, tmp_path, monkeypatch):
        """工具明显变慢 → 检测到 slow 异常。"""
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        sid = "slow-session"
        set_session(sid)
        # 创建基线：快工具
        # 创建基线：快工具（多用几次以主导基线）
        for _ in range(20):
            record_tool_call("grep_code", 50, True)
        flush()

        # 下一会话：慢工具
        sid2 = "slow-session-2"
        set_session(sid2)
        for _ in range(5):
            record_tool_call("grep_code", 500, True)
        flush()
        anomalies = detect_anomalies(sid2)
        set_session(sid2)
        for _ in range(5):
            record_tool_call("grep_code", 500, True)
        flush()
        anomalies = detect_anomalies(sid2)
        slow = [a for a in anomalies if a["type"] == "slow"]
        assert len(slow) > 0
        assert slow[0]["tool"] == "grep_code"

    def test_high_volume_detected(self, tmp_path, monkeypatch):
        """某工具本次调用数远超其它会话 → 检测到 high_volume（撒网式重复调用）。"""
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        # 基线：grep 每会话约 3 次
        for sid in ("base1", "base2"):
            set_session(sid)
            for _ in range(3):
                record_tool_call("grep_code", 50, True)
            flush()
        # 本会话：撒网 15 次
        set_session("scatter")
        for _ in range(15):
            record_tool_call("grep_code", 50, True)
        flush()
        anomalies = detect_anomalies("scatter")
        hv = [a for a in anomalies if a["type"] == "high_volume"]
        assert len(hv) > 0
        assert hv[0]["tool"] == "grep_code"
        assert "15" in hv[0]["detail"]

    def test_normal_volume_not_flagged(self, tmp_path, monkeypatch):
        """工具本就高频（其它会话也这么多）→ 不报，避免误伤正常重活。"""
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        for sid in ("h1", "h2"):
            set_session(sid)
            for _ in range(14):
                record_tool_call("read_file", 10, True)
            flush()
        set_session("h3")
        for _ in range(15):
            record_tool_call("read_file", 10, True)
        flush()
        anomalies = detect_anomalies("h3")
        assert [a for a in anomalies if a["type"] == "high_volume"] == []

    def test_below_floor_not_flagged(self, tmp_path, monkeypatch):
        """低于绝对地板（短会话）→ 不报，避免噪声。"""
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        set_session("short")
        for _ in range(8):
            record_tool_call("grep_code", 10, True)
        flush()
        anomalies = detect_anomalies("short")
        assert [a for a in anomalies if a["type"] == "high_volume"] == []

    def test_signal_window_counts_only_eligible_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        for sid, failures in [("clean-1", 0), ("bad", 5), ("clean-2", 0)]:
            set_session(sid)
            for index in range(5):
                record_tool_call("write_file", 10, index >= failures)
            flush()
        set_session("not-exposed")
        for _ in range(5):
            record_tool_call("read_file", 10, True)
        flush()

        window = get_anomaly_signal_window(
            "write_file",
            "high_failure",
            since="2020-01-01T00:00:00+00:00",
            limit=3,
        )

        assert window["sessions_observed"] == 3
        assert window["occurrences"] == 1
        assert window["occurrence_rate"] == pytest.approx(1 / 3, abs=0.001)

class TestInternal:
    def test_read_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        set_session("s1")
        record_tool_call("t1", 10, True)
        flush()
        records = _read_session("s1")
        assert len(records) == 1
        assert records[0]["tool"] == "t1"

    def test_discover_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tracker._STATS_DIR", tmp_path)
        set_session("a")
        record_tool_call("t", 1, True)
        flush()
        set_session("b")
        record_tool_call("t", 1, True)
        flush()
        sessions = _discover_sessions()
        assert "a" in sessions
        assert "b" in sessions
