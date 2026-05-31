"""测试 Phase 1-3 缓存优化：API消息构建 + 工具结果压缩 + 对话历史压缩。"""

from src.llm import (
    _build_api_messages,
    _compress_tool_result,
    _compact_context,
    _is_topic_switch,
    _make_brief_summary,
    _TOOL_RESULT_COMPRESS_THRESHOLD,
)


class TestBuildApiMessages:
    """测试 _build_api_messages：系统消息排前，_volatile 过滤，非系统保持追加顺序。"""

    def test_system_messages_first(self):
        msgs = [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "system", "content": "s1"},
        ]
        api = _build_api_messages(msgs)
        assert api[0]["role"] == "system"
        assert api[1]["role"] == "user"
        assert api[2]["role"] == "assistant"

    def test_volatile_is_filtered(self):
        msgs = [
            {"role": "user", "content": "u"},
            {"role": "system", "content": "mode", "_volatile": True},
        ]
        api = _build_api_messages(msgs)
        volatile_in_api = [m for m in api if m.get("_volatile")]
        assert len(volatile_in_api) == 0

    def test_non_volatile_system_kept(self):
        msgs = [
            {"role": "user", "content": "u"},
            {"role": "system", "content": "static rule"},
        ]
        api = _build_api_messages(msgs)
        assert len(api) == 2
        assert api[0]["role"] == "system"

    def test_mixed_volatile_and_normal(self):
        msgs = [
            {"role": "user", "content": "1"},
            {"role": "system", "content": "s1", "_volatile": True},
            {"role": "assistant", "content": "2"},
            {"role": "system", "content": "s2"},
        ]
        api = _build_api_messages(msgs)
        roles = [m["role"] for m in api]
        # 所有 system 消息（含 volatile）排在前面，user/assistant 在后面
        assert roles == ["system", "system", "user", "assistant"]
        assert api[0]["content"] == "s1"  # volatile system 也纳入前缀
        assert api[1]["content"] == "s2"

    def test_non_system_order_preserved(self):
        msgs = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1", "tool_calls": []},
            {"role": "tool", "content": "t1"},
            {"role": "user", "content": "u2"},
        ]
        api = _build_api_messages(msgs)
        non_system = [m for m in api if m["role"] != "system"]
        assert [m["content"] for m in non_system] == ["u1", "a1", "t1", "u2"]

    def test_all_system(self):
        msgs = [
            {"role": "system", "content": "s1"},
            {"role": "system", "content": "s2"},
        ]
        api = _build_api_messages(msgs)
        assert len(api) == 2


class TestCompactContext:
    """测试 _compact_context：对话历史压缩。"""

    def test_only_system_not_compressed(self):
        msgs = [{"role": "system", "content": "规则"}]
        assert len(_compact_context(msgs, "hello")) == 1

    def test_short_not_compressed(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        assert len(_compact_context(msgs, "继续")) == 3

    def test_topic_switch_compresses_all(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "旧话题"},
            {"role": "assistant", "content": "旧回答"},
        ]
        result = _compact_context(msgs, "换个话题")
        assert len(result) <= 2  # 规则 + 归档消息

    def test_topic_switch_not_triggered(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "继续讨论同一个话题"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _compact_context(msgs, "继续讨论同一个话题")
        assert len(result) == 3

    def test_large_messages_truncated(self):
        big = "X" * 100000
        msgs = [{"role": "system", "content": "规则"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题{i} " + big})
            msgs.append({"role": "assistant", "content": f"回答{i} " + big})
            msgs.append({"role": "tool", "content": big[:100]})
        result = _compact_context(msgs, "继续做")
        assert len(result) < len(msgs)
        assert any("⏪" in m.get("content", "") for m in result if m["role"] == "system")


    def test_empty_input(self):
        assert _build_api_messages([]) == []


class TestCompressToolResult:
    """测试 _compress_tool_result：阈值压缩 + 工具类型提示语。"""

    def test_short_result_not_compressed(self):
        assert _compress_tool_result("read_file", "hello") == "hello"

    def test_empty_not_compressed(self):
        assert _compress_tool_result("read_file", "") == ""

    def test_exact_boundary(self):
        text = "x" * _TOOL_RESULT_COMPRESS_THRESHOLD
        assert _compress_tool_result("read_file", text) == text

    def test_one_over_boundary_has_hint(self):
        text = "y" * (_TOOL_RESULT_COMPRESS_THRESHOLD + 1)
        compressed = _compress_tool_result("read_file", text)
        assert "已压缩" in compressed
        assert "3001" in compressed

    def test_large_result_truncated(self):
        text = "z" * 5000
        compressed = _compress_tool_result("read_file", text)
        assert len(compressed) < len(text)
        assert "已压缩" in compressed

    def test_read_file_hint(self):
        text = "z" * 5000
        compressed = _compress_tool_result("read_file", text)
        assert "read_file" in compressed

    def test_read_file_lines_hint(self):
        text = "a" * 5000
        compressed = _compress_tool_result("read_file_lines", text)
        assert "read_file" in compressed

    def test_search_web_hint(self):
        text = "b" * 5000
        compressed = _compress_tool_result("search_web", text)
        assert "搜索" in compressed or "search" in compressed

    def test_read_page_hint(self):
        text = "c" * 5000
        compressed = _compress_tool_result("read_page", text)
        assert "搜索" in compressed or "搜索" in compressed

    def test_generic_tool_hint(self):
        text = "d" * 5000
        compressed = _compress_tool_result("other_tool", text)
        assert "已压缩" in compressed
        assert "read_file" not in compressed

    def test_git_status_not_compressed(self):
        text = "e" * 5000
        assert _compress_tool_result("git_status", text) == text

    def test_git_diff_not_compressed(self):
        text = "f" * 5000
        assert _compress_tool_result("git_diff", text) == text

    def test_check_project_not_compressed(self):
        text = "g" * 5000
        assert _compress_tool_result("check_project", text) == text

    def test_docs_sync_check_not_compressed(self):
        text = "h" * 5000
        assert _compress_tool_result("docs_sync_check", text) == text

    def test_compress_shows_size_info(self):
        text = "i" * 10000
        compressed = _compress_tool_result("generic", text)
        assert "10000" in compressed
        assert str(_TOOL_RESULT_COMPRESS_THRESHOLD) in compressed
