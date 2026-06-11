"""门通行证审计测试：HEAD 树哈希 vs 钩子记录的核对逻辑 + 启动注入接线。

背景：8b02143..50c78c3 用 --no-verify 绕门、6 红测试入库。
动作级拦截（exec.py 操守牙）拦不全所有绕门路径，本审计键于
「提交的树是否有通行证」这个不变量，事后兜底全部路径。
"""

import pytest

import src.gate_audit as gate_audit
import src.tasks as tasks
from src.gaps import GapReport


def _fake_git(tmp_path, head_tree="tree_abc"):
    def fake(args):
        if args[:2] == ["rev-parse", "--git-dir"]:
            return str(tmp_path)
        if args == ["rev-parse", "HEAD^{tree}"]:
            return head_tree
        if args == ["rev-parse", "--short", "HEAD"]:
            return "abc1234"
        return ""
    return fake


def test_silent_when_record_file_missing(tmp_path, monkeypatch):
    """记录文件不存在 = 机制未部署/新克隆 → 静默，不误报。"""
    monkeypatch.setattr(gate_audit, "_git_line", _fake_git(tmp_path))
    assert gate_audit.head_gate_notice() == ""


def test_silent_when_head_tree_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_audit, "_git_line", _fake_git(tmp_path))
    (tmp_path / "ctg-gate-passed").write_text(
        "tree_other\ntree_abc\n", encoding="utf-8")
    assert gate_audit.head_gate_notice() == ""


def test_notice_when_head_tree_absent(tmp_path, monkeypatch):
    """有记录但 HEAD 树不在其中 → 该提交没过门，必须提醒。"""
    monkeypatch.setattr(gate_audit, "_git_line", _fake_git(tmp_path))
    (tmp_path / "ctg-gate-passed").write_text("tree_other\n", encoding="utf-8")
    notice = gate_audit.head_gate_notice()
    assert "绕过" in notice
    assert "abc1234" in notice
    assert "pytest" in notice  # 提醒里给出验证动作，不只报警


def test_silent_when_not_a_git_repo(monkeypatch):
    """`git` 不可用/不在仓库 → 审计静默，绝不阻塞启动。"""
    monkeypatch.setattr(gate_audit, "_git_line", lambda _a: "")
    assert gate_audit.head_gate_notice() == ""


def test_notice_injected_at_session_start(tmp_path, monkeypatch):
    """接线：make_task_context_message 会话首次调用注入审计提醒。"""
    sentinel = "GATE_AUDIT_SENTINEL_4417"
    monkeypatch.setattr(gate_audit, "head_gate_notice", lambda: sentinel)
    monkeypatch.setattr("src.gaps.detect_all_gaps", lambda top_n=5: GapReport())
    monkeypatch.setattr("src.tracker.get_latest_reflections", lambda limit=3: [])
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", tmp_path / "current.md")
    monkeypatch.setattr(tasks, "_gaps_reported", False)

    msg = tasks.make_task_context_message()
    assert msg is not None
    assert sentinel in msg["content"]


def test_notice_only_once_per_session(tmp_path, monkeypatch):
    """第二次调用不再重复注入（同 gaps 的每会话一次语义）。"""
    sentinel = "GATE_AUDIT_SENTINEL_4417"
    monkeypatch.setattr(gate_audit, "head_gate_notice", lambda: sentinel)
    monkeypatch.setattr("src.gaps.detect_all_gaps", lambda top_n=5: GapReport())
    monkeypatch.setattr("src.tracker.get_latest_reflections", lambda limit=3: [])
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", tmp_path / "current.md")
    monkeypatch.setattr(tasks, "_gaps_reported", False)

    first = tasks.make_task_context_message()
    assert first is not None and sentinel in first["content"]
    second = tasks.make_task_context_message()
    assert second is None or sentinel not in second["content"]


@pytest.fixture(autouse=True)
def _restore_gaps_flag():
    """测试间复位会话级标志，避免串扰其他模块的测试。"""
    saved = tasks._gaps_reported
    yield
    tasks._gaps_reported = saved
