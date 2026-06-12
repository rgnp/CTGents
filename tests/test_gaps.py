"""gaps.py 测试 — 差距检测与报告格式化。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gaps import (
    Gap,
    GapReport,
    _gap_score,
    _prioritize,
    _static_suggestion,
    detect_all_gaps,
    format_gap_report,
    get_gap_by_index,
    get_last_report,
)


class TestGapDataclass:
    def test_creation(self):
        g = Gap(
            source="static", gap_type="dead_code", severity="high",
            detail="test:1 - unused function",
            affected_files=["test.py"], suggestion="Delete it",
            confidence=0.95, actionable=True,
        )
        assert g.source == "static"
        assert g.severity == "high"

    def test_defaults(self):
        g = Gap(source="test", gap_type="", severity="low", detail="x")
        assert g.affected_files == []
        assert g.confidence == 0.0
        assert g.actionable is True


class TestGapReport:
    def test_creation(self):
        report = GapReport(
            gaps=[Gap(source="s", gap_type="t", severity="med", detail="d")],
            sources_scanned=3,
            sources_failed=1,
            failures=["coverage: error"],
        )
        assert len(report.gaps) == 1
        assert report.sources_scanned == 3

    def test_defaults(self):
        report = GapReport()
        assert report.gaps == []


class TestGapScore:
    def test_high_severity_scores_higher(self):
        g_high = Gap(source="s", gap_type="t", severity="high", detail="d",
                      confidence=0.9, actionable=True)
        g_low = Gap(source="s", gap_type="t", severity="low", detail="d",
                     confidence=0.9, actionable=True)
        assert _gap_score(g_high) > _gap_score(g_low)

    def test_not_actionable_penalized(self):
        g_yes = Gap(source="s", gap_type="t", severity="high", detail="d",
                     confidence=0.9, actionable=True)
        g_no = Gap(source="s", gap_type="t", severity="high", detail="d",
                    confidence=0.9, actionable=False)
        assert _gap_score(g_yes) > _gap_score(g_no)


class TestPrioritize:
    def test_sorts_and_deduplicates(self):
        gaps = [
            Gap(source="a", gap_type="same", severity="high", detail="d1",
                affected_files=["f.py"], confidence=0.9),
            Gap(source="b", gap_type="same", severity="low", detail="d2",
                affected_files=["f.py"], confidence=0.5),
        ]
        result = _prioritize(gaps, top_n=5)
        # 去重：同文件同类型只保留一个
        assert len(result) <= 2


class TestStaticSuggestion:
    def test_dead_code(self):
        assert "Delete" in _static_suggestion("dead_code")

    def test_complexity(self):
        assert "Extract" in _static_suggestion("complexity")

    def test_unknown(self):
        assert _static_suggestion("unknown") == "Review and fix."


class TestDetectAllGaps:
    def test_returns_report(self):
        report = detect_all_gaps()
        assert isinstance(report, GapReport)
        assert isinstance(report.gaps, list)

    def test_get_last_report(self):
        detect_all_gaps()
        report = get_last_report()
        assert report is not None
        assert isinstance(report, GapReport)

    def test_get_gap_by_index(self):
        detect_all_gaps()
        # index 0 或超范围的返回 None
        g = get_gap_by_index(100)
        assert g is None


class TestFormatGapReport:
    def test_empty_report(self):
        report = GapReport(gaps=[])
        result = format_gap_report(report)
        assert "未发现" in result

    def test_with_gaps(self):
        report = GapReport(gaps=[
            Gap(source="static", gap_type="dead_code", severity="high",
                detail="test:1 - unused", affected_files=["test.py"],
                suggestion="Delete", confidence=0.95, actionable=True),
        ])
        result = format_gap_report(report)
        assert "test.py" in result
        assert "Delete" in result
