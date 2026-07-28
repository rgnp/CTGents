"""git_status 的 porcelain 状态分类。

回归：未跟踪文件('??' → worktree 位也是 '?')曾被误判为 unstaged，
因为 `worktree != " "` 排在 untracked 判断之前。
"""

import src.tools.git as git_tools
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


def test_git_push_rejects_force_to_main(monkeypatch):
    """专用 git_push 也必须守住主干强推边界，不能只靠 run_command guard。"""
    calls: list[list[str]] = []

    monkeypatch.setattr(git_tools, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(git_tools, "_get_current_branch", lambda _path: "main")

    def fake_git(args, _path=None, **_kwargs):
        calls.append(args)
        if args == ["remote"]:
            return {"success": True, "stdout": "origin\n", "stderr": "", "returncode": 0}
        return {"success": True, "stdout": "", "stderr": "", "returncode": 0}

    monkeypatch.setattr(git_tools, "_git", fake_git)

    assert "P2" in git_tools.git_push(force=True)
    assert "P2" in git_tools.git_push(branch="HEAD:refs/heads/master", force=True)
    assert not any(call and call[0] == "push" for call in calls)


def test_git_push_allows_force_to_feature(monkeypatch):
    """功能分支仍可显式强推。"""
    calls: list[list[str]] = []

    monkeypatch.setattr(git_tools, "_is_git_repo", lambda _path: True)

    def fake_git(args, _path=None, **_kwargs):
        calls.append(args)
        if args == ["remote"]:
            return {"success": True, "stdout": "origin\n", "stderr": "", "returncode": 0}
        return {"success": True, "stdout": "ok", "stderr": "", "returncode": 0}

    monkeypatch.setattr(git_tools, "_git", fake_git)

    assert "已推送" in git_tools.git_push(branch="feature/safe-fix", force=True)
    assert ["push", "origin", "feature/safe-fix", "--force"] in calls
