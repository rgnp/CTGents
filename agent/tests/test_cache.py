"""测试 Phase 1-2 缓存优化：_build_api_messages + _compress_tool_result。"""

from src.llm import (
    _build_api_messages,
    _compress_tool_result,
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
        assert roles == ["system", "user", "assistant"]

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
