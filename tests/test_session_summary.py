"""session_summary.py 测试 — 会话摘要提取/写入/搜索 + LLM 层 + 前缀索引。"""

import json
import tempfile
from pathlib import Path

# _llm_summarize 在此绑定原函数（收集期 import 早于 conftest 的 autouse 补丁；
# 补丁只改模块属性，不影响这里已捕获的引用）——用于直接测 LLM 层本身。
from src.session_summary import (
    _build_digest,
    _extract_decisions,
    _extract_files,
    _extract_topics,
    _llm_summarize,
    _load_summary,
    build_sessions_index,
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


# ═══════════════════════════════════════════════════════════
# LLM 摘要层
# ═══════════════════════════════════════════════════════════


class _FakeBackend:
    """chat_non_stream 假后端：返回固定 content，或 raise。"""

    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.called = False

    def chat_non_stream(self, messages, on_token=None, tools=None, max_tokens=None):
        self.called = True
        if self._exc:
            raise self._exc
        return self._content, None


_LONG_MSGS = [
    _make_msg("user", "我们继续讨论 GraphWorld 的复现问题，上次说到评测指标对不上，"
                      "这次把 occupancy 那条线也捋一下，重点看 3D 一致性怎么量化。"),
    _make_msg("assistant", "好，先对齐评测协议，再看 OccWorld 的做法。"),
]


class TestLLMSummarize:
    def test_parses_json_from_noisy_output(self, monkeypatch):
        """LLM 输出前后有杂文字也能抠出 JSON。"""
        from src.llm import AVAILABLE_MODELS
        fake = _FakeBackend(
            '好的，以下是摘要：{"topics": ["GraphWorld 复现", "3D 一致性"], '
            '"narrative": "对齐了评测协议。", "unfinished": "occupancy 线还没捋完"} 完毕')
        monkeypatch.setitem(AVAILABLE_MODELS, "flash", fake)
        result = _llm_summarize(_LONG_MSGS)
        assert result is not None
        assert "GraphWorld 复现" in result["topics"]
        assert result["unfinished"] == "occupancy 线还没捋完"

    def test_backend_error_returns_none(self, monkeypatch):
        from src.llm import AVAILABLE_MODELS
        monkeypatch.setitem(AVAILABLE_MODELS, "flash", _FakeBackend(exc=RuntimeError("网络挂了")))
        assert _llm_summarize(_LONG_MSGS) is None

    def test_garbage_output_returns_none(self, monkeypatch):
        from src.llm import AVAILABLE_MODELS
        monkeypatch.setitem(AVAILABLE_MODELS, "flash", _FakeBackend("这不是 JSON"))
        assert _llm_summarize(_LONG_MSGS) is None

    def test_tiny_session_skips_llm(self, monkeypatch):
        """太短的会话不值一次调用——backend 根本不该被碰。"""
        from src.llm import AVAILABLE_MODELS
        fake = _FakeBackend('{"topics": ["x"], "narrative": "", "unfinished": ""}')
        monkeypatch.setitem(AVAILABLE_MODELS, "flash", fake)
        assert _llm_summarize([_make_msg("user", "hi")]) is None
        assert not fake.called

    def test_build_digest_keeps_head_and_tail(self):
        """超长文字稿保头尾、中间标记省略（SUMMARY 是 frozen dataclass，直接超默认阈值）。"""
        msgs = [_make_msg("user", f"第{i}条开头 " + "内容" * 245) for i in range(40)]
        digest = _build_digest(msgs)
        assert "第0条开头" in digest
        assert "第39条开头" in digest
        assert "（中间省略）" in digest

    def test_digest_includes_tool_names_not_results(self):
        msgs = [
            _make_msg("user", "改一下文件"),
            _make_tool_call("write_file", {"path": "src/a.py", "content": "巨大的文件内容" * 100}),
            _make_msg("tool", "工具结果原文不该进稿" * 50),
        ]
        digest = _build_digest(msgs)
        assert "write_file" in digest
        assert "src/a.py" in digest
        assert "工具结果原文不该进稿" not in digest


class TestExtractSummaryLLMPath:
    def test_llm_result_wins(self, monkeypatch):
        monkeypatch.setattr(
            "src.session_summary._llm_summarize",
            lambda _m: {"topics": ["世界模型选题"], "narrative": "定了从评测切入。",
                        "unfinished": "还差 OccWorld 精读"})
        summary = extract_summary(_LONG_MSGS)
        assert summary["source"] == "llm"
        assert summary["topics"] == ["世界模型选题"]
        assert summary["unfinished"] == "还差 OccWorld 精读"
        assert "定了从评测切入" in summary["text"]
        # 用户原话仍进脉络（保中文检索命中）
        assert "GraphWorld" in summary["text"]

    def test_fallback_to_rules(self):
        """Conftest 已把 _llm_summarize 打成 None → 规则层。"""
        msgs = _LONG_MSGS + [
            _make_tool_call("remember", {"name": "graphworld-eval", "content": "x", "type": "knowledge"})]
        summary = extract_summary(msgs)
        assert summary["source"] == "rules"
        assert any("graphworld" in t for t in summary["topics"])

    def test_use_llm_false_skips(self, monkeypatch):
        called = []
        monkeypatch.setattr("src.session_summary._llm_summarize",
                            lambda _m: called.append(1) or None)
        extract_summary(_LONG_MSGS, use_llm=False)
        assert not called

    def test_rules_unfinished_from_need_user(self):
        msgs = _LONG_MSGS + [_make_tool_call("need_user", {"question": "评测协议选 A 还是 B？"})]
        summary = extract_summary(msgs)
        assert summary["unfinished"] == "评测协议选 A 还是 B？"


# ═══════════════════════════════════════════════════════════
# 未竟事项栏：写入 ↔ 解析回环
# ═══════════════════════════════════════════════════════════


class TestUnfinishedSection:
    def test_roundtrip(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)
        summary = {"topics": ["世界模型"], "decisions": [], "files": [],
                   "text": "聊了选题。", "unfinished": "OccWorld 还没读完"}
        path = write_summary("2026-01-02-100000", summary)
        loaded = _load_summary(Path(path))
        assert loaded["unfinished"] == "OccWorld 还没读完"

    def test_empty_unfinished_normalized(self, monkeypatch):
        """无未竟事项 → 写「（无）」占位 → 读回空串（不把占位当内容）。"""
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)
        summary = {"topics": ["世界模型"], "decisions": [], "files": [],
                   "text": "聊了选题。", "unfinished": ""}
        path = write_summary("2026-01-02-110000", summary)
        loaded = _load_summary(Path(path))
        assert loaded["unfinished"] == ""

    def test_old_format_without_section(self, monkeypatch):
        """旧格式摘要（无未竟事项栏）→ 解析为空串，不炸。"""
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "2026-01-01-000000.md"
        f.write_text("# 会话 x\n- 话题: 旧话题\n\n## 对话脉络\n旧脉络\n", encoding="utf-8")
        loaded = _load_summary(f)
        assert loaded["unfinished"] == ""
        assert loaded["topics"] == "旧话题"


# ═══════════════════════════════════════════════════════════
# 前缀会话索引
# ═══════════════════════════════════════════════════════════


class TestBuildSessionsIndex:
    def _write(self, monkeypatch, tmp, sid, topics, unfinished=""):
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)
        write_summary(sid, {"topics": topics, "decisions": [], "files": [],
                            "text": "脉络", "unfinished": unfinished})

    def test_index_lines(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        self._write(monkeypatch, tmp, "2026-06-30-100000", ["GraphWorld", "评测"],
                    unfinished="occupancy 线没捋完")
        self._write(monkeypatch, tmp, "2026-06-29-100000", ["TUI 重构"])
        idx = build_sessions_index()
        assert idx is not None
        assert "2026-06-30 · GraphWorld" in idx
        assert "未竟: occupancy 线没捋完" in idx
        assert "2026-06-29 · TUI 重构" in idx
        assert "search_sessions" in idx  # 索引自带取详情的路标

    def test_empty_dir_returns_none(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        monkeypatch.setattr("src.session_summary._KNOWLEDGE_SESSIONS_DIR", tmp)
        assert build_sessions_index() is None

    def test_unfinished_only_on_recent(self, monkeypatch):
        """未竟事项只附在最近 index_unfinished 场上，老会话不带。"""
        import src.session_summary as ss
        tmp = Path(tempfile.mkdtemp())
        for day in range(1, 12):
            self._write(monkeypatch, tmp, f"2026-06-{day:02d}-100000",
                        [f"话题{day}"], unfinished=f"没做完{day}")
        idx = build_sessions_index()
        # 倒序：06-11 最新，最近 index_unfinished(默认8) 场带未竟，更老的（06-01 等）不带
        assert "未竟: 没做完11" in idx
        assert idx.count("未竟:") == min(ss.SUMMARY.index_unfinished, 11)
        oldest_line = next(ln for ln in idx.splitlines() if "2026-06-01" in ln)
        assert "未竟" not in oldest_line
