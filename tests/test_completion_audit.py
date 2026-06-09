"""收尾取证自检：audit_completion 纯函数 + 它依赖的跨模块输出契约（C16）。

audit 跨模块读 file/git/exec 的成功 marker 来分类"绿测/改动"，天然脆。
下半截 TestOutputContracts 用真实工具断言这些 marker 仍在——谁改了那些模块的
输出格式（"已写入:"/"退出码:" 等），这里立刻报警，而非让自检悄悄失效。
"""
from __future__ import annotations

import json

from src.completion_audit import (
    _EDIT_OK,
    _EXIT_PREFIX,
    _NUDGE,
    _WRITE_OK,
    audit_completion,
)
from src.tools.exec import run_command
from src.tools.file import edit_file_lines, write_file

# ── 构造日志消息 ──────────────────────────────────────────────

def _call(cid: str, name: str, **args) -> dict:
    """一条带 tool_calls 的 assistant 消息（绿测要靠它拿到命令串）。"""
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "function": {"name": name, "arguments": json.dumps(args)}}]}


def _result(cid: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": content, "_tool_name": name}


def _py_edit(cid: str = "e1") -> dict:
    return _result(cid, "write_file", "已写入: D:\\project\\src\\foo.py（10 字符）")


# ── audit_completion 判定 ─────────────────────────────────────

def test_no_events_returns_none():
    assert audit_completion([]) is None
    assert audit_completion([{"role": "user", "content": "hi"}]) is None


def test_edit_without_green_is_stale():
    assert audit_completion([_py_edit()]) == _NUDGE


def test_edit_then_pytest_green_is_fresh():
    log = [
        _py_edit(),
        _call("c1", "run_command", command="pytest -q"),
        _result("c1", "run_command", "===== 3 passed in 0.1s ====="),
    ]
    assert audit_completion(log) is None


def test_edit_then_commit_green_is_fresh():
    log = [_py_edit(), _result("g1", "git_commit", "✅ 提交成功\n\nfeat: x")]
    assert audit_completion(log) is None


def test_green_then_edit_is_stale():
    """先绿测，之后又改了 .py → 结论过期。"""
    log = [
        _call("c1", "run_command", command="pytest -q"),
        _result("c1", "run_command", "===== 3 passed ====="),
        _py_edit("e2"),
    ]
    assert audit_completion(log) == _NUDGE


def test_failed_pytest_not_green():
    """带'退出码:'前缀（非零退出）不算绿测 → 仍 stale。"""
    log = [
        _py_edit(),
        _call("c1", "run_command", command="pytest -q"),
        _result("c1", "run_command", "退出码: 1\n\n===== 1 failed ====="),
    ]
    assert audit_completion(log) == _NUDGE


def test_edit_file_lines_counts_as_edit():
    log = [_result("e1", "edit_file_lines", "已编辑: D:\\project\\src\\m.py\n操作: replace")]
    assert audit_completion(log) == _NUDGE


def test_non_py_write_ignored():
    """改 .md 不动测试覆盖面 → 不算代码改动。"""
    log = [_result("e1", "write_file", "已写入: D:\\project\\notes.md（5 字符）")]
    assert audit_completion(log) is None


def test_failed_write_not_counted():
    """写入失败 → 盘上没变 → 不该 stale。"""
    log = [_result("e1", "write_file", "写入失败: 权限不足")]
    assert audit_completion(log) is None


def test_non_pytest_run_command_not_green():
    """改完只跑了个非测试命令（ls）→ 没验证 → 仍 stale。"""
    log = [
        _py_edit(),
        _call("c1", "run_command", command="ls -la"),
        _result("c1", "run_command", "foo.py\nbar.py"),
    ]
    assert audit_completion(log) == _NUDGE


def test_failed_commit_not_green():
    """提交被 pre-commit 拦下 → 不算绿 → 仍 stale。"""
    log = [_py_edit(), _result("g1", "git_commit", "提交失败（可能是 pre-commit 质量门禁未通过）")]
    assert audit_completion(log) == _NUDGE


# ── 输出契约不变量（C16：审计依赖这些 marker，钉死它们）──────────

class TestOutputContracts:
    """用真实工具断言审计所依赖的输出 marker 仍然成立。"""

    def test_run_command_failure_has_exit_prefix(self):
        # 'python -c "exit(1)"' 无 shell 元字符，可过 run_command 的拆分门禁
        out = run_command('python -c "exit(1)"')
        assert out.startswith(_EXIT_PREFIX), "run_command 失败须前置退出码——绿测判定靠它"

    def test_run_command_success_no_exit_prefix(self):
        out = run_command('python -c "exit(0)"')
        assert not out.startswith(_EXIT_PREFIX), "退 0 不得带退出码前缀（否则绿测全被漏判）"

    def test_write_file_success_marker(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = write_file(str(tmp_path / "x.py"), "a = 1\n")
        assert out.startswith(_WRITE_OK), "write_file 成功 marker 变了 → 改动检测失效"

    def test_edit_file_lines_success_marker(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\nb = 2\n", encoding="utf-8")
        out = edit_file_lines(str(f), "replace", 2, 2, "b = 20")
        assert out.startswith(_EDIT_OK), "edit_file_lines 成功 marker 变了 → 改动检测失效"

    # git_commit 成功（"✅ 提交成功"）需真 repo + 绿树，太重 → 不做集成测试；
    # 该 marker 的耦合由 completion_audit._COMMIT_OK 处的注释 + 本类记录在案。
