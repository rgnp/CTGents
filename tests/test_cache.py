"""测试 Phase 1-3 缓存优化：API消息构建 + 工具结果压缩 + 对话历史压缩。"""

from src.llm import (
    _compress_tool_result,
    _compact_context,
    _is_topic_switch,
    _make_brief_summary,
    _TOOL_RESULT_COMPRESS_THRESHOLD,
)


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
        # append-only: 3 + 1 summary = 4
        assert len(_compact_context(msgs, "继续")) == 4

    def test_topic_switch_compresses_all(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "旧话题"},
            {"role": "assistant", "content": "旧回答"},
        ]
        result = _compact_context(msgs, "换个话题")
        # append-only: 3 + 1 boundary marker = 4
        assert len(result) == 4
        assert any("前一话题已结束" in m.get("content", "") for m in result)

    def test_topic_switch_not_triggered(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "继续讨论同一个话题"},
            {"role": "assistant", "content": "好的"},
        ]
        result = _compact_context(msgs, "继续讨论同一个话题")
        # append-only: 3 + 1 summary = 4
        assert len(result) == 4

    def test_large_messages_truncated(self):
        big = "X" * 100000
        msgs = [{"role": "system", "content": "规则"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题{i} " + big})
            msgs.append({"role": "assistant", "content": f"回答{i} " + big})
            msgs.append({"role": "tool", "content": big[:100]})
        result = _compact_context(msgs, "继续做")
        # append-only: 不会减少消息，只追加摘要
        assert len(result) >= len(msgs)
        assert any("⏪" in m.get("content", "") for m in result if m["role"] == "system")


class TestCompressToolResult:
    """测试 _compress_tool_result：阈值压缩 + 工具类型提示语。"""

    def test_short_result_not_compressed(self):
        assert _compress_tool_result("grep_code", "hello") == "hello"

    def test_empty_not_compressed(self):
        assert _compress_tool_result("grep_code", "") == ""

    def test_exact_boundary(self):
        text = "x" * _TOOL_RESULT_COMPRESS_THRESHOLD
        assert _compress_tool_result("grep_code", text) == text

    def test_one_over_boundary_has_hint(self):
        text = "y" * (_TOOL_RESULT_COMPRESS_THRESHOLD + 1)
        compressed = _compress_tool_result("grep_code", text)
        assert "已压缩" in compressed
        assert str(_TOOL_RESULT_COMPRESS_THRESHOLD + 1) in compressed

    def test_large_result_truncated(self):
        text = "z" * 5000
        compressed = _compress_tool_result("grep_code", text)
        assert len(compressed) < len(text)
        assert "已压缩" in compressed

    def test_read_file_exempt_from_compression(self):
        """read_file 豁免硬截断：即使超过阈值也不压缩。"""
        text = "a" * 5000
        result = _compress_tool_result("read_file", text)
        assert result == text
        assert "已压缩" not in result

    def test_read_file_lines_exempt_from_compression(self):
        """read_file_lines 豁免硬截断。"""
        text = "b" * 5000
        result = _compress_tool_result("read_file_lines", text)
        assert result == text
        assert "已压缩" not in result

    def test_search_web_hint(self):
        text = "c" * 5000
        compressed = _compress_tool_result("search_web", text)
        assert "搜索" in compressed or "search" in compressed

    def test_read_page_hint(self):
        text = "d" * 5000
        compressed = _compress_tool_result("read_page", text)
        assert "搜索" in compressed or "search" in compressed

    def test_generic_tool_hint(self):
        text = "e" * 5000
        compressed = _compress_tool_result("other_tool", text)
        assert "已压缩" in compressed
        assert "read_file" not in compressed

    def test_generic_tool_hint(self):
        text = "d" * 5000
        compressed = _compress_tool_result("other_tool", text)
        assert "已压缩" in compressed
        assert "read_file" not in compressed

    def test_compress_shows_size_info(self):
        text = "i" * 10000
        compressed = _compress_tool_result("generic", text)
        assert "10000" in compressed
        assert str(_TOOL_RESULT_COMPRESS_THRESHOLD) in compressed
