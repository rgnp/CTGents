"""差距检测框架测试：多信号源汇聚 + 排序 + 去重 + 缓存查询。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.gaps import (
    Gap,
    GapReport,
    _deduplicate,
    _detect_performance_gaps,
    _detect_static_gaps,
    _gap_score,
    _make_fix_prompt,
    _prioritize,
    detect_all_gaps,
    format_gap_report,
    get_gap_by_index,
    get_last_report,
)

pytestmark = pytest.mark.slow


def test_gap_defaults():
    g = Gap(source="test", gap_type="test", severity="medium", detail="test")
    assert g.affected_files == []
    assert g.suggestion == ""
    assert g.confidence == 0.0
    assert g.actionable is True


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


# 探测器


def test_performance_detector_returns_list():
    result = _detect_performance_gaps()
    assert isinstance(result, list)


def test_static_detector_returns_list():
    result = _detect_static_gaps()
    assert isinstance(result, list)
    assert len(result) <= 3


# 格式化


def test_format_empty_report():
    assert "未发现" in format_gap_report(GapReport())


def test_format_report_with_gaps():
    report = GapReport(
        gaps=[Gap(source="performance", gap_type="slow", severity="medium", detail="test tool",
                   affected_files=["src/t.py"],
                   suggestion="sug", confidence=0.8)],
        sources_scanned=3,
    )
    output = format_gap_report(report)
    assert "主动进化" in output
    assert "test tool" in output


def test_format_report_with_failures():
    report = GapReport(
        gaps=[Gap(source="t", gap_type="t", severity="medium", detail="d")],
        sources_scanned=3, sources_failed=1, failures=["coverage: timeout"],
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


# 集成


def test_detect_all_gaps_does_not_crash():
    report = detect_all_gaps(top_n=3)
    assert isinstance(report, GapReport)
    assert report.sources_scanned == 3
