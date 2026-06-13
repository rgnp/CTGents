"""session_summary 模块测试。"""

import json

from src.session_summary import (
    _extract_actions,
    _extract_decisions,
    _extract_feedback,
    _extract_products,
    _extract_tags,
    _make_summary,
    _slug_from_actions,
    write_session_summary,
)


def _tool_result(tool_name: str, result: dict) -> dict:
    return {"role": "tool", "content": json.dumps(result), "_tool_name": tool_name}


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant(content: str | None = None, tool_calls: list[dict] | None = None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tc(name: str) -> dict:
    return {"function": {"name": name}}


class TestExtractTags:
    def test_empty(self):
        assert _extract_tags([]) == ["#对话"]

    def test_write_py_triggers_code_tag(self):
        msgs = [
            _assistant(tool_calls=[_tc("write_file")]),
            _tool_result("write_file", {"path": "src/main.py"}),
        ]
        assert "#代码" in _extract_tags(msgs)
        assert "#修改" in _extract_tags(msgs)

    def test_compression_tag(self):
        msgs = [_user("压缩太长了，少而精")]
        assert "#压缩" in _extract_tags(msgs)

    def test_remember_tag(self):
        msgs = [_assistant("remember('key', 'val')")]
        assert "#记忆系统" in _extract_tags(msgs)


class TestExtractActions:
    def test_commit_extracted(self):
        msgs = [
            _tool_result("git_commit", {"hash": "abc1234", "message": "fix: crash on null"}),
        ]
        actions = _extract_actions(msgs)
        assert "fix: crash on null" in actions

    def test_write_extracted(self):
        msgs = [
            _tool_result("write_file", {"path": "project/src/llm.py"}),
        ]
        actions = _extract_actions(msgs)
        assert any("src/llm.py" in a for a in actions)

    def test_max_five_actions(self):
        msgs = [_tool_result("write_file", {"path": f"tests/test_{i}.py"}) for i in range(10)]
        actions = _extract_actions(msgs)
        assert len(actions) <= 6  # 5 + "…"


class TestExtractDecisions:
    def test_directive_captured(self):
        msgs = [_user("改这里，用 summary 而非 raw")]
        decisions = _extract_decisions(msgs)
        assert any("summary" in d for d in decisions)

    def test_question_excluded(self):
        msgs = [_user("做完了吗？又中断了")]
        decisions = _extract_decisions(msgs)
        assert not decisions  # 含问号应排除

    def test_future_directive(self):
        msgs = [_user("以后每次 commit 前都跑 lint")]
        decisions = _extract_decisions(msgs)
        assert any("以后" in d for d in decisions)


class TestExtractFeedback:
    def test_correction_detected(self):
        msgs = [_user("不对，应该用 A 而不是 B")]
        fb = _extract_feedback(msgs)
        assert any("纠正" in f for f in fb)

    def test_nudge_detected(self):
        msgs = [_user("做完了吗？")]
        fb = _extract_feedback(msgs)
        assert any("催促" in f for f in fb)

    def test_preference_detected(self):
        msgs = [_user("我喜欢简洁的回答")]
        fb = _extract_feedback(msgs)
        assert any("偏好" in f for f in fb)


class TestExtractProducts:
    def test_hash_extracted(self):
        msgs = [_tool_result("git_commit", {"hash": "2eae3db", "message": "ok"})]
        assert "2eae3db" in _extract_products(msgs)

    def test_non_hash_ignored(self):
        msgs = [_tool_result("run_command", {"output": "xyz1234"})]
        assert _extract_products(msgs) == []


class TestSlugFromActions:
    def test_commit_first_word(self):
        assert _slug_from_actions(["compact: 摘要极简化"]) == "compact"

    def test_write_file_slug(self):
        assert _slug_from_actions(["写 src/llm.py"]) == "llm"

    def test_empty_default(self):
        assert _slug_from_actions([]) == "session"


class TestMakeSummary:
    def test_minimal(self):
        summary = _make_summary([])
        assert "# 会话 #" in summary
        assert "#对话" in summary

    def test_includes_products(self):
        msgs = [_tool_result("git_commit", {"hash": "2eae3db", "message": "compact"})]
        summary = _make_summary(msgs)
        assert "2eae3db" in summary


class TestWriteSessionSummary:
    def test_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.session_summary.KNOWLEDGE_DIR",
            tmp_path,
        )
        msgs = [
            _user("做压缩"),
            _assistant(tool_calls=[_tc("git_commit")]),
            _tool_result("git_commit", {"hash": "abc1234", "message": "compact: done"}),
        ]
        filename = write_session_summary(msgs)
        assert filename is not None
        assert (tmp_path / filename).exists()
        content = (tmp_path / filename).read_text(encoding="utf-8")
        assert "compact" in content.lower()
        assert "abc1234" in content
