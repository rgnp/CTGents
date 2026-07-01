"""session_summary.py 测试 — 会话摘要提取/写入/搜索。"""

import json
import tempfile
from pathlib import Path

from src.session_summary import (
    _extract_decisions,
    _extract_files,
    _extract_topics,
    _load_summary,
    extract_summary,
    search_sessions,
    summarize_session,
    write_summary,
)

# ── 测试用消息 ──


def _make_msg(role: str, content: str = "", **extra) -> dict:
    m = {"role": role, "content": content, **extra}
    return m


def _make_tool_call(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            }
        }],
    }


# ═══════════════════════════════════════════════════════════
# 话题提取
# ═══════════════════════════════════════════════════════════


class TestExtractTopics:
    def test_from_remember_names(self):
        msgs = [
            _make_tool_call("remember", {"name": "world-model-survey", "content": "x", "type": "knowledge"}),
            _make_tool_call("remember", {"name": "graphworld-contradiction", "content": "x", "type": "knowledge"}),
        ]
        topics = _extract_topics(msgs)
        assert "world model survey" in topics
        assert "graphworld contradiction" in topics

    def test_from_task_done(self):
        msgs = [
            _make_tool_call("task_done", {"summary": "完成世界模型综述。新增5篇论文"}),
        ]
        topics = _extract_topics(msgs)
        assert any("世界模型" in t or "5篇论文" in t for t in topics)

    def test_empty_messages(self):
        assert _extract_topics([]) == []

    def test_english_tech_terms(self):
        msgs = [
            _make_msg("user", "GraphWorld and Latent-WAM show different results"),
            _make_msg("assistant", "GraphWorld uses scene-conditioned prediction"),
        ]
        topics = _extract_topics(msgs)
        assert "GraphWorld" in topics


# ═══════════════════════════════════════════════════════════
# 决策提取
# ═══════════════════════════════════════════════════════════


class TestExtractDecisions:
    def test_remember(self):
        msgs = [_make_tool_call("remember", {"name": "test-memory", "content": "x", "type": "knowledge"})]
        decisions = _extract_decisions(msgs)
        assert len(decisions) == 1
        assert decisions[0]["type"] == "记忆"
        assert "test memory" in decisions[0]["detail"]

    def test_task_done(self):
        msgs = [_make_tool_call("task_done", {"summary": "修复了工具调用超时"})]
        decisions = _extract_decisions(msgs)
        assert len(decisions) == 1
        assert decisions[0]["type"] == "任务完成"

    def test_need_user(self):
        msgs = [_make_tool_call("need_user", {"question": "选方案 A 还是 B？"})]
        decisions = _extract_decisions(msgs)
        assert len(decisions) == 1
        assert decisions[0]["type"] == "待决策"

    def test_empty(self):
        assert _extract_decisions([]) == []


# ═══════════════════════════════════════════════════════════
# 文件提取
# ═══════════════════════════════════════════════════════════


class TestExtractFiles:
    def test_write_and_replace(self):
        msgs = [
            _make_tool_call("write_file", {"path": "src/a.py", "content": "x"}),
            _make_tool_call("replace_in_file", {"path": "src/b.py", "old": "y", "new": "z"}),
        ]
        files = _extract_files(msgs)
        assert files == ["src/a.py", "src/b.py"]

    def test_dedup(self):
        msgs = [
            _make_tool_call("replace_in_file", {"path": "src/a.py", "old": "x", "new": "y"}),
            _make_tool_call("replace_in_file", {"path": "src/a.py", "old": "y", "new": "z"}),
        ]
        files = _extract_files(msgs)
        assert files == ["src/a.py"]

    def test_skips_other_tools(self):
        msgs = [
            _make_tool_call("grep_code", {"pattern": "x"}),
            _make_tool_call("run_command", {"command": "ls"}),
        ]
        assert _extract_files(msgs) == []


# ═══════════════════════════════════════════════════════════
# 摘要写入 & 搜索
# ═══════════════════════════════════════════════════════════


class TestWriteAndSearch:
    def test_write_and_search_roundtrip(self, monkeypatch):
        """写入一条摘要 → 搜索能找回。"""
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)

        msgs = [
            _make_msg("user", "我想深入讨论世界模型在自动驾驶仿真中的应用"),
            _make_msg("assistant", "好的，世界模型是生成式仿真的关键技术"),
            _make_tool_call("remember", {"name": "world-model-discussion", "content": "x", "type": "knowledge"}),
        ]
        summary = extract_summary(msgs)
        path = write_summary("2026-01-01-120000", summary)
        assert path is not None

        # 用英文查 topic
        results = search_sessions("world model")
        assert len(results) >= 1
        assert any("world model" in r["topics"] for r in results)

        # 用中文查（会命中用户消息中的中文原文）
        results_cn = search_sessions("世界模型")
        assert len(results_cn) >= 1

    def test_search_no_match(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)

        # 空目录
        assert search_sessions("whatever") == []

    def test_empty_session_no_write(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)

        # 无话题无决策无文件 → 不写
        msgs = [_make_msg("user", "hi"), _make_msg("assistant", "hello")]
        summary = extract_summary(msgs)
        result = write_summary("2026-01-01-120000", summary)
        assert result is None  # 跳过空会话

    def test_summarize_session_integration(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)

        msgs = [
            _make_msg("user", "帮我修一下 src/main.py 的 bug"),
            _make_tool_call("replace_in_file", {"path": "src/main.py", "old": "bug", "new": "fix"}),
            _make_tool_call("task_done", {"summary": "修了 main.py 的 import 错误"}),
        ]
        path = summarize_session(msgs, "2026-01-01-130000")
        assert path is not None
        content = Path(path).read_text(encoding="utf-8")
        assert "src/main.py" in content
        assert "import" in content

    def test_load_summary(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)

        msgs = [
            _make_msg("user", "聊一聊自动驾驶仿真"),
            _make_tool_call("remember", {"name": "ad-simulation-tools", "content": "x", "type": "knowledge"}),
        ]
        path = summarize_session(msgs, "2026-01-01-140000")
        loaded = _load_summary(Path(path))
        assert loaded is not None
        assert "ad simulation tools" in loaded["topics"]
        assert loaded["session_id"] == "2026-01-01-140000"
