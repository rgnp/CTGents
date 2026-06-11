"""测试 Phase 1-3 缓存优化：API消息构建 + 工具结果压缩 + 对话历史压缩。"""

import pytest

import src.llm as llm

pytestmark = pytest.mark.slow

_THRESHOLD = llm._TOOL_RESULT_COMPRESS_THRESHOLD


class TestCompactContext:
    """_compact_context：滑窗压缩（超阈值驱旧，短上下文不动）。"""

    def test_only_system_not_compressed(self):
        msgs = [{"role": "system", "content": "规则"}]
        assert len(llm._compact_context(msgs, "hello")) == 1

    def test_short_not_compressed(self):
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        assert len(llm._compact_context(msgs, "继续")) == 3

    def _make_compaction_messages(self, n: int, big: str) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": "规则"}]
        for i in range(n):
            msgs.append({"role": "user", "content": f"问题{i} " + big})
            msgs.append({"role": "assistant", "content": f"回答{i} " + big})
        return msgs

    def test_topic_keywords_do_not_block_compaction(self, monkeypatch):
        """含"算了/换个"等口语词照常压缩——关键词换话题已删除。"""
        monkeypatch.setattr(llm, "_make_brief_summary", lambda msgs, max_len=500: "测试摘要")
        monkeypatch.setattr(llm, "_COMPACT_THRESHOLD", 0.001)

        big = "X" * 5000
        msgs = self._make_compaction_messages(5, big)
        result = llm._compact_context(msgs, "算了，换个话题")
        assert len(result) < len(msgs), "命中口语关键词也必须正常驱逐"
        assert not any("前一话题已结束" in m.get("content", "") for m in result)

    def test_eviction_never_orphans_tool_messages(self, monkeypatch):
        """驱逐边界对齐 user 消息开头——不切断 tool 配对。"""
        monkeypatch.setattr(llm, "_make_brief_summary", lambda msgs, max_len=500: "测试摘要")

        big = "Y" * 60000
        msgs: list[dict] = [{"role": "system", "content": "规则"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"任务{i} " + big})
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "t", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big[:50]})
            msgs.append({"role": "assistant", "content": f"结果{i}"})
        result = llm._compact_context(msgs, "继续", force=True)
        assert len(result) < len(msgs)
        non_system = [m for m in result if m.get("role") != "system"]
        assert non_system[0]["role"] == "user"
        ids_seen: set[str] = set()
        for m in result:
            for tc in m.get("tool_calls") or []:
                ids_seen.add(tc["id"])
            if m.get("role") == "tool":
                assert m["tool_call_id"] in ids_seen, "孤儿 tool 消息（API 会 400）"

    def test_large_context_evicts_old_messages(self, monkeypatch):
        """大型上下文触发滑窗压缩：旧消息被驱替为摘要。"""
        monkeypatch.setattr(llm, "_make_brief_summary", lambda msgs, max_len=500: "测试摘要")
        monkeypatch.setattr(llm, "_COMPACT_THRESHOLD", 0.001)

        big = "X" * 5000
        msgs = self._make_compaction_messages(5, big)
        msgs.extend([{"role": "tool", "content": big[:100]} for _ in range(5)])
        result = llm._compact_context(msgs, "继续做")
        assert len(result) < len(msgs), f"压缩应减少消息: {len(result)} vs {len(msgs)}"
        assert any("⏪" in m.get("content", "") for m in result if m["role"] == "system")


class TestCompressToolResult:
    """_compress_tool_result：head+tail 截断 + 省略标记。"""

    def test_short_result_not_compressed(self):
        assert llm._compress_tool_result("grep_code", "hello") == "hello"

    def test_empty_not_compressed(self):
        assert llm._compress_tool_result("grep_code", "") == ""

    def test_exact_boundary(self):
        text = "x" * _THRESHOLD
        assert llm._compress_tool_result("grep_code", text) == text

    def test_barely_over_boundary_not_enlarged(self):
        text = "y" * (_THRESHOLD + 1)
        assert llm._compress_tool_result("grep_code", text) == text

    def test_large_result_keeps_head_and_tail(self):
        half = _THRESHOLD // 2
        text = "H" * half + "m" * 3000 + "T" * half
        compressed = llm._compress_tool_result("grep_code", text)
        assert len(compressed) < len(text)
        assert compressed.startswith("H" * half)
        assert compressed.endswith("T" * half)
        assert "省略" in compressed

    def test_read_file_exempt_from_compression(self):
        text = "a" * 5000
        result = llm._compress_tool_result("read_file", text)
        assert result == text
        assert "省略" not in result

    def test_read_file_lines_exempt_from_compression(self):
        text = "b" * 5000
        result = llm._compress_tool_result("read_file_lines", text)
        assert result == text
        assert "省略" not in result

    def test_search_tools_plain_marker(self):
        for tool in ("search_web", "read_page"):
            compressed = llm._compress_tool_result(tool, "c" * 5000)
            assert "省略" in compressed
            assert "read_file" not in compressed

    def test_generic_tool_hints_retrieval_path(self):
        compressed = llm._compress_tool_result("other_tool", "e" * 5000)
        assert "省略" in compressed
        assert "read_file" in compressed

    def test_compress_shows_size_info(self):
        text = "i" * 10000
        compressed = llm._compress_tool_result("generic", text)
        omitted = 10000 - (_THRESHOLD // 2) * 2
        assert "10000" in compressed
        assert str(omitted) in compressed
