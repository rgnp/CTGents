"""research.py 纯逻辑回归。

research.py 此前零测试，且大多是网络抓取（arxiv/tavily）难测。这里只锁两个
纯逻辑 helper：
1. _arxiv_year — 年份判定曾用 startswith('250')，漏掉 10/11/12 月的 id。
2. _iter_query_pairs — 畸形条目曾让 `for a, q in list` 解包抛 ValueError 崩溃。
"""
from __future__ import annotations

from src.tools.research import _arxiv_year, _iter_query_pairs

# ── _arxiv_year：年份只看前两位 ──────────────────────────────

def test_year_early_months_2025():
    assert _arxiv_year("2501.00001") == "2025"
    assert _arxiv_year("2509.12345") == "2025"


def test_year_late_months_2025_regression():
    """回归：10/11/12 月的 id 曾被 startswith('250') 漏掉。"""
    assert _arxiv_year("2510.00001") == "2025", "10 月论文被漏判"
    assert _arxiv_year("2511.12345") == "2025"
    assert _arxiv_year("2512.99999") == "2025"


def test_year_2026():
    assert _arxiv_year("2601.00001") == "2026"
    assert _arxiv_year("2610.00001") == "2026", "2026 年 10 月也曾被漏掉"


def test_year_out_of_range():
    assert _arxiv_year("2412.00001") is None
    assert _arxiv_year("2712.00001") is None


def test_display_label_uses_two_digit_year():
    """显示标签取年份后两位：2510 应显示 25，而非旧逻辑误判的 26。"""
    assert (_arxiv_year("2510.00001") or "????")[2:] == "25"


# ── _iter_query_pairs：跳过畸形条目，不崩 ────────────────────

def test_pairs_normal():
    assert _iter_query_pairs([["a", "b"], ["c", "d"]]) == [("a", "b"), ("c", "d")]


def test_pairs_skips_malformed():
    out = _iter_query_pairs([["a", "b"], ["x"], "bad", ["c", "d", "e"], []])
    assert out == [("a", "b")], f"应跳过所有结构不符条目: {out}"


def test_pairs_empty():
    assert _iter_query_pairs([]) == []
