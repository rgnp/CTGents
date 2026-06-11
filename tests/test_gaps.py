"""差距检测框架测试：多信号源汇聚 + 排序 + 去重。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gaps import (
    Gap,
    GapReport,
    _deduplicate,
    _detect_performance_gaps,
    _detect_static_gaps,
    _gap_score,
    _prioritize,
    detect_all_gaps,
    format_gap_report,
)

# ═══════════════════════════════════════════════════════════════
# Gap 数据类
# ═══════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════
# 排序
# ═══════════════════════════════════════════════════════════════


def test_gap_score_higher_for_more_severe():
    high = Gap(source="test", gap_type="t", severity="high", detail="", confidence=0.9, actionable=True)
    low = Gap(source="test", gap_type="t", severity="low", detail="", confidence=0.9, actionable=True)
    assert _gap_score(high) > _gap_score(low)


def test_gap_score_higher_for_actionable():
    a = Gap(source="test", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=True)
    b = Gap(source="test", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=False)
    assert _gap_score(a) > _gap_score(b)


def test_gap_score_higher_for_confident():
    a = Gap(source="test", gap_type="t", severity="medium", detail="", confidence=0.9, actionable=True)
    b = Gap(source="test", gap_type="t", severity="medium", detail="", confidence=0.3, actionable=True)
    assert _gap_score(a) > _gap_score(b)


# ═══════════════════════════════════════════════════════════════
# 去重
# ═══════════════════════════════════════════════════════════════


def test_deduplicate_removes_same_file_type():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a",
            affected_files=["src/a.py"]),
        Gap(source="s", gap_type="dead_code", severity="high", detail="b",
            affected_files=["src/a.py"]),
    ]
    result = _deduplicate(gaps)
    assert len(result) == 1


def test_deduplicate_keeps_different_types_same_file():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a",
            affected_files=["src/a.py"]),
        Gap(source="s", gap_type="complexity", severity="high", detail="b",
            affected_files=["src/a.py"]),
    ]
    result = _deduplicate(gaps)
    assert len(result) == 2


def test_deduplicate_keeps_same_type_different_files():
    gaps = [
        Gap(source="s", gap_type="dead_code", severity="high", detail="a",
            affected_files=["src/a.py"]),
        Gap(source="s", gap_type="dead_code", severity="high", detail="b",
            affected_files=["src/b.py"]),
    ]
    result = _deduplicate(gaps)
    assert len(result) == 2


# ═══════════════════════════════════════════════════════════════
# 优先排序
# ═══════════════════════════════════════════════════════════════


def test_prioritize_caps_at_top_n():
    gaps = [
        Gap(source="test", gap_type=f"t{i}", severity="high", detail=str(i),
            confidence=0.9, actionable=True)
        for i in range(10)
    ]
    result = _prioritize(gaps, top_n=3)
    assert len(result) == 3


def test_prioritize_handles_empty():
    assert _prioritize([], top_n=5) == []


# ═══════════════════════════════════════════════════════════════
# 探测器存在性
# ═══════════════════════════════════════════════════════════════


def test_performance_detector_returns_list():
    result = _detect_performance_gaps()
    assert isinstance(result, list)


def test_static_detector_returns_list():
    result = _detect_static_gaps()
    assert isinstance(result, list)
    # 最多 3 个
    assert len(result) <= 3


# ═══════════════════════════════════════════════════════════════
# 格式化
# ═══════════════════════════════════════════════════════════════


def test_format_empty_report():
    report = GapReport()
    output = format_gap_report(report)
    assert "未发现" in output


def test_format_report_with_gaps():
    report = GapReport(
        gaps=[
            Gap(source="performance", gap_type="slow_test", severity="medium",
                detail="test tool 慢了 5x", affected_files=["src/test.py"],
                suggestion="测试建议", confidence=0.8, actionable=True),
        ],
        sources_scanned=3,
    )
    output = format_gap_report(report)
    assert "主动进化" in output
    assert "test tool 慢了 5x" in output
    assert "测试建议" in output


def test_format_report_with_failures():
    report = GapReport(
        gaps=[Gap(source="test", gap_type="t", severity="medium", detail="d")],
        sources_scanned=3,
        sources_failed=1,
        failures=["coverage: timeout"],
    )
    output = format_gap_report(report)
    assert "信号源失败" in output
    assert "coverage" in output


# ═══════════════════════════════════════════════════════════════
# 集成：detect_all_gaps 不丢异常
# ═══════════════════════════════════════════════════════════════


def test_detect_all_gaps_does_not_crash():
    """即使个别信号源失败，detect_all_gaps 也不应抛异常。"""
    report = detect_all_gaps(top_n=3)
    assert isinstance(report, GapReport)
    assert report.sources_scanned == 3
