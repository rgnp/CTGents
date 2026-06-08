"""git_status 的 porcelain 状态分类。

回归：未跟踪文件('??' → worktree 位也是 '?')曾被误判为 unstaged，
因为 `worktree != " "` 排在 untracked 判断之前。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.git import _classify_porcelain


def test_untracked_not_unstaged():
    """回归 bug：'??' 必须归 untracked，而非 unstaged。"""
    assert _classify_porcelain("?", "?") == "untracked"


def test_staged():
    assert _classify_porcelain("M", " ") == "staged"
    assert _classify_porcelain("A", " ") == "staged"


def test_unstaged():
    assert _classify_porcelain(" ", "M") == "unstaged"


def test_conflict():
    assert _classify_porcelain("U", "U") == "conflict"
    assert _classify_porcelain("A", "U") == "conflict"


def test_staged_and_unstaged_counts_as_staged():
    """既暂存又改动（MM）汇总为 staged（与原行为一致）。"""
    assert _classify_porcelain("M", "M") == "staged"


def test_clean_is_empty():
    assert _classify_porcelain(" ", " ") == ""
