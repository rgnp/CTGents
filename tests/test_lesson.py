"""失败模式学习测试 — 四指纹检测器 + 注入引擎。"""

import json

import pytest

from src.lesson import (
    Lesson,
    _detect_precommit_rejection,
    _detect_repeated_file_edit,
    _detect_signature_drift,
    _detect_tool_arg_error,
    extract_lessons,
    inject_lesson_context,
    match_lessons_for_action,
    save_lessons,
)

# ═══════════════════════════════════════════════════════════════
# 构造测试数据辅助
# ═══════════════════════════════════════════════════════════════

def _assistant(content: str, tool_calls: list[dict] | None = None) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": tool_calls or []}


def _tool(call_id: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content, "_tool_name": name}


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


# ═══════════════════════════════════════════════════════════════
# 签名变更漂移
# ═══════════════════════════════════════════════════════════════

def test_signature_drift_empty_log():
    assert _detect_signature_drift([]) == []


def test_signature_drift_detects_pattern():
    """改 src → pytest 失败 → 改 tests → 仍失败 → 提取教训。"""
    log = [
        _assistant("", [_tool_call("c1", "write_file",
                                    {"path": "src/llm.py", "content": "..."})]),
        _tool("c1", "write_file", "ok"),
        _assistant("", [_tool_call("c2", "run_command",
                                    {"command": "pytest tests/"})]),
        _tool("c2", "run_command", "FAILED test_llm.py"),
        _assistant("", [_tool_call("c3", "edit_file_lines",
                                    {"path": "tests/test_llm.py", "start_line": 5, "new_lines": "..."})]),
        _tool("c3", "edit_file_lines", "ok"),
        _assistant("", [_tool_call("c4", "run_command",
                                    {"command": "pytest tests/"})]),
        _tool("c4", "run_command", "FAILED test_llm.py again"),
    ]
    lessons = _detect_signature_drift(log)
    assert len(lessons) == 1
    assert lessons[0].fingerprint == "signature_drift"
    assert "grep_code" in lessons[0].content


def test_signature_drift_no_failures_no_lesson():
    log = [
        _assistant("", [_tool_call("c1", "write_file",
                                    {"path": "src/llm.py", "content": "..."})]),
        _tool("c1", "write_file", "ok"),
        _assistant("", [_tool_call("c2", "write_file",
                                    {"path": "tests/test_llm.py", "content": "..."})]),
        _tool("c2", "write_file", "ok"),
    ]
    assert _detect_signature_drift(log) == []


# ═══════════════════════════════════════════════════════════════
# 重复编辑
# ═══════════════════════════════════════════════════════════════

def test_repeated_edit_detects_3_plus():
    log = [
        _assistant("", [_tool_call("c1", "write_file",
                                    {"path": "tests/test_llm.py", "content": "a"})]),
        _tool("c1", "write_file", "ok"),
        _assistant("", [_tool_call("c2", "edit_file_lines",
                                    {"path": "tests/test_llm.py", "start_line": 1, "new_lines": "b"})]),
        _tool("c2", "edit_file_lines", "ok"),
        _assistant("", [_tool_call("c3", "write_file",
                                    {"path": "tests/test_llm.py", "content": "c"})]),
        _tool("c3", "write_file", "ok"),
    ]
    lessons = _detect_repeated_file_edit(log)
    assert len(lessons) == 1
    assert lessons[0].fingerprint == "repeated_edit"


def test_repeated_edit_under_3_no_lesson():
    log = [
        _assistant("", [_tool_call("c1", "write_file",
                                    {"path": "tests/test_llm.py", "content": "a"})]),
        _tool("c1", "write_file", "ok"),
        _assistant("", [_tool_call("c2", "write_file",
                                    {"path": "tests/test_llm.py", "content": "b"})]),
        _tool("c2", "write_file", "ok"),
    ]
    assert _detect_repeated_file_edit(log) == []


# ═══════════════════════════════════════════════════════════════
# 工具参数错误
# ═══════════════════════════════════════════════════════════════

def test_tool_arg_error_detects():
    """工具先返回 error，后又成功 → 提取教训。"""
    log = [
        _assistant("", [_tool_call("c1", "run_command",
                                    {"command": "bad-command"})]),
        _tool("c1", "run_command", json.dumps({"error": "command not found"})),
        _assistant("", [_tool_call("c2", "run_command",
                                    {"command": "py -m pytest"})]),
        _tool("c2", "run_command", "628 passed"),
    ]
    lessons = _detect_tool_arg_error(log)
    assert len(lessons) == 1
    assert lessons[0].fingerprint == "tool_arg_error"


def test_tool_arg_error_no_failure_no_lesson():
    log = [
        _assistant("", [_tool_call("c1", "run_command",
                                    {"command": "py -m pytest"})]),
        _tool("c1", "run_command", "628 passed"),
    ]
    assert _detect_tool_arg_error(log) == []


# ═══════════════════════════════════════════════════════════════
# 预提交被拒
# ═══════════════════════════════════════════════════════════════

def test_precommit_rejection_detects():
    log = [
        _assistant("", [_tool_call("c1", "git_commit",
                                    {"message": "fix"})]),
        _tool("c1", "git_commit", "pre-commit 拒绝提交: ruff check 未通过"),
    ]
    lessons = _detect_precommit_rejection(log)
    assert len(lessons) == 1
    assert lessons[0].fingerprint == "precommit_rejection"


def test_precommit_clean_no_lesson():
    log = [
        _assistant("", [_tool_call("c1", "git_commit",
                                    {"message": "fix"})]),
        _tool("c1", "git_commit", "✅ 提交成功"),
    ]
    assert _detect_precommit_rejection(log) == []


# ═══════════════════════════════════════════════════════════════
# 主提取入口
# ═══════════════════════════════════════════════════════════════

def test_extract_lessons_multi_fingerprint():
    """多检测器同时命中，去重。"""
    log = [
        _assistant("", [_tool_call("c1", "write_file",
                                    {"path": "tests/x.py", "content": "a"})]),
        _tool("c1", "write_file", "ok"),
        _assistant("", [_tool_call("c2", "write_file",
                                    {"path": "tests/x.py", "content": "b"})]),
        _tool("c2", "write_file", "ok"),
        _assistant("", [_tool_call("c3", "write_file",
                                    {"path": "tests/x.py", "content": "c"})]),
        _tool("c3", "write_file", "ok"),
        _assistant("", [_tool_call("c4", "git_commit",
                                    {"message": "x"})]),
        _tool("c4", "git_commit", "pre-commit 拒绝提交"),
    ]
    lessons = extract_lessons(log)
    assert len(lessons) >= 1  # at least repeated_edit
    fingerprints = {le.fingerprint for le in lessons}
    assert "repeated_edit" in fingerprints
    assert "precommit_rejection" in fingerprints


# ═══════════════════════════════════════════════════════════════
# 注入引擎
# ═══════════════════════════════════════════════════════════════

def test_inject_lesson_context_no_match_noop(tmp_path, monkeypatch):
    """没有匹配的教训时，不向 log 追加任何消息。"""
    import src.config as cfg
    import src.lesson as lesson_mod
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(lesson_mod, "MEMORY_DIR", str(tmp_path), raising=False)
    log: list[dict] = []
    inject_lesson_context(log, "write_file", {"path": "tests/unknown.py"})
    assert len(log) == 0


def test_match_lessons_no_memories(tmp_path, monkeypatch):
    import src.config as cfg
    import src.lesson as lesson_mod
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(lesson_mod, "MEMORY_DIR", str(tmp_path), raising=False)
    assert match_lessons_for_action("write_file src/llm.py") == []
    assert match_lessons_for_action("") == []


# ═══════════════════════════════════════════════════════════════
# 端到端: 提取 → 保存 → 召回
# ═══════════════════════════════════════════════════════════════

@pytest.mark.slow
def test_extract_save_and_recall(tmp_path, monkeypatch):
    """提取教训 → 存入记忆 → 能通过匹配召回。"""
    import src.config as cfg
    import src.tools.memory as memtools

    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(memtools, "MEMORY_DIR", str(mem_dir))

    # 1) 构造日志：预提交被拒
    log = [
        _assistant("", [_tool_call("c1", "git_commit",
                                    {"message": "fix"})]),
        _tool("c1", "git_commit", "pre-commit ⛔ 拒绝提交"),
    ]
    lessons = extract_lessons(log)
    assert len(lessons) == 1

    # 2) 保存
    n = save_lessons(lessons)
    assert n == 1

    # 3) 匹配召回
    matches = match_lessons_for_action("git_commit message=fix")
    assert len(matches) >= 1
    assert any("pre-commit" in m.lower() for m in matches)

    # 4) 注入
    log2: list[dict] = []
    inject_lesson_context(log2, "git_commit", {"message": "fix"})
    assert len(log2) == 1
    assert "经验提醒" in log2[0]["content"]


@pytest.mark.slow
def test_duplicate_lesson_increments_counter(tmp_path, monkeypatch):
    """同名教训再次保存 → times_encountered 递增。"""
    import src.config as cfg
    import src.tools.memory as memtools

    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(cfg, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(memtools, "MEMORY_DIR", str(mem_dir))

    le = Lesson(
        name="lesson-test-repeat",
        fingerprint="precommit_rejection",
        severity="high",
        content="## 模式\n测试重复保存。\n",
    )

    save_lessons([le])
    save_lessons([le])

    # 读取文件确认 times_encountered
    import re
    text = (mem_dir / "lesson-test-repeat.md").read_text(encoding="utf-8")
    match = re.search(r"times_encountered:\s*(\d+)", text)
    assert match
    assert int(match.group(1)) == 2
