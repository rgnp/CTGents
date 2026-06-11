"""测试 Phase 1-3 缓存优化：API消息构建 + 工具结果压缩 + 对话历史压缩。"""

from src.llm import (
    _TOOL_RESULT_COMPRESS_THRESHOLD,
    _compact_context,
    _compress_tool_result,
)


class TestCompactContext:
    """测试 _compact_context：滑窗压缩（超阈值驱旧，短上下文不动）。"""

    def test_only_system_not_compressed(self):
        msgs = [{"role": "system", "content": "规则"}]
        assert len(_compact_context(msgs, "hello")) == 1

    def test_short_not_compressed(self):
        """短上下文不压缩——无需追加摘要，消息数不变。"""
        msgs = [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
        assert len(_compact_context(msgs, "继续")) == 3

    def test_topic_keywords_do_not_block_compaction(self):
        """含"算了/换个"等口语词的输入照常压缩——关键词换话题机制已删除。

        旧实现：超阈值时若输入命中子串关键词，只加标记不驱逐 → 该压不压、
        上下文继续膨胀；且子串匹配是机械化判断题（auto-plan 同病）。
        """
        big = "X" * 300000
        msgs = [{"role": "system", "content": "规则"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题{i} " + big})
            msgs.append({"role": "assistant", "content": f"回答{i} " + big})
        result = _compact_context(msgs, "算了，换个话题")
        assert len(result) < len(msgs), "命中口语关键词也必须正常驱逐"
        assert not any("前一话题已结束" in m.get("content", "") for m in result)

    def test_eviction_never_orphans_tool_messages(self):
        """驱逐边界对齐 user 消息开头：保留区不得以 tool/assistant 起头。

        孤儿 tool 消息（前面没有对应 assistant tool_calls）会被 API 400 拒收。
        旧实现按比例硬切，边界可落在 assistant(tool_calls) 与 tool 结果之间。
        """
        big = "Y" * 60000
        msgs = [{"role": "system", "content": "规则"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"任务{i} " + big})
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "t", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": big[:50]})
            msgs.append({"role": "assistant", "content": f"结果{i}"})
        result = _compact_context(msgs, "继续", force=True)
        assert len(result) < len(msgs)
        # 保留区第一条非 system 消息必须是 user（一轮的起点）
        non_system = [m for m in result if m.get("role") != "system"]
        assert non_system[0]["role"] == "user", (
            f"保留区以 {non_system[0]['role']} 起头，tool 配对可能被切断"
        )
        # 每条 tool 消息都必须有前置的 assistant tool_calls 配对
        ids_seen: set[str] = set()
        for m in result:
            for tc in m.get("tool_calls") or []:
                ids_seen.add(tc["id"])
            if m.get("role") == "tool":
                assert m["tool_call_id"] in ids_seen, "孤儿 tool 消息（API 会 400）"

    def test_large_context_evicts_old_messages(self):
        """大型上下文触发滑窗压缩：旧消息被驱替为摘要，总数减少。"""
        big = "X" * 300000  # 足够大以触发 80% 阈值
        msgs = [{"role": "system", "content": "规则"}]
        for i in range(5):
            msgs.append({"role": "user", "content": f"问题{i} " + big})
            msgs.append({"role": "assistant", "content": f"回答{i} " + big})
            msgs.append({"role": "tool", "content": big[:100]})
        result = _compact_context(msgs, "继续做")
        # 滑窗压缩：旧消息被驱替，总数应明显减少
        assert len(result) < len(msgs), (
            f"压缩应减少消息: {len(result)} vs {len(msgs)}"
        )
        assert any("⏪" in m.get("content", "") for m in result if m["role"] == "system")


class TestCompressToolResult:
    """测试 _compress_tool_result：head+tail 截断 + 省略标记。"""

    def test_short_result_not_compressed(self):
        assert _compress_tool_result("grep_code", "hello") == "hello"

    def test_empty_not_compressed(self):
        assert _compress_tool_result("grep_code", "") == ""

    def test_exact_boundary(self):
        text = "x" * _TOOL_RESULT_COMPRESS_THRESHOLD
        assert _compress_tool_result("grep_code", text) == text

    def test_barely_over_boundary_not_enlarged(self):
        """省略量小于标记开销时不压缩——压缩绝不能让结果变大。"""
        text = "y" * (_TOOL_RESULT_COMPRESS_THRESHOLD + 1)
        assert _compress_tool_result("grep_code", text) == text

    def test_large_result_keeps_head_and_tail(self):
        """大结果保留首尾各半阈值——尾部常含结论（如 pytest 摘要行）。"""
        half = _TOOL_RESULT_COMPRESS_THRESHOLD // 2
        text = "H" * half + "m" * 3000 + "T" * half
        compressed = _compress_tool_result("grep_code", text)
        assert len(compressed) < len(text)
        assert compressed.startswith("H" * half)
        assert compressed.endswith("T" * half)
        assert "省略" in compressed

    def test_read_file_exempt_from_compression(self):
        """read_file 豁免硬截断：即使超过阈值也不压缩。"""
        text = "a" * 5000
        result = _compress_tool_result("read_file", text)
        assert result == text
        assert "省略" not in result

    def test_read_file_lines_exempt_from_compression(self):
        """read_file_lines 豁免硬截断。"""
        text = "b" * 5000
        result = _compress_tool_result("read_file_lines", text)
        assert result == text
        assert "省略" not in result

    def test_search_tools_plain_marker(self):
        """search_web / read_page 用纯省略标记，不带 read_file 取回提示。"""
        for tool in ("search_web", "read_page"):
            compressed = _compress_tool_result(tool, "c" * 5000)
            assert "省略" in compressed
            assert "read_file" not in compressed

    def test_generic_tool_hints_retrieval_path(self):
        """通用工具的标记带取回提示（read_file 指定行范围）。"""
        compressed = _compress_tool_result("other_tool", "e" * 5000)
        assert "省略" in compressed
        assert "read_file" in compressed

    def test_compress_shows_size_info(self):
        """标记中含省略字符数与原始总字符数。"""
        text = "i" * 10000
        compressed = _compress_tool_result("generic", text)
        omitted = 10000 - (_TOOL_RESULT_COMPRESS_THRESHOLD // 2) * 2
        assert "10000" in compressed
        assert str(omitted) in compressed
