"""测试 Phase 1-3 缓存优化：API消息构建 + 工具结果压缩 + 对话历史压缩。"""

import pytest

import src.llm as llm

pytestmark = pytest.mark.slow

_THRESHOLD = llm._TOOL_RESULT_COMPRESS_THRESHOLD


def setup_function():
    """Reset compaction state between tests."""
    llm._previous_summary = None
    llm._ineffective_compression_count = 0


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
        monkeypatch.setattr(llm, "_make_brief_summary",
                            lambda msgs, max_len=500, previous_summary=None: "测试摘要")
        monkeypatch.setattr(llm, "_COMPACT_THRESHOLD", 0.001)

        big = "X" * 5000
        msgs = self._make_compaction_messages(5, big)
        result = llm._compact_context(msgs, "算了，换个话题")
        assert len(result) < len(msgs), "命中口语关键词也必须正常驱逐"
        assert not any("前一话题已结束" in m.get("content", "") for m in result)

    def test_eviction_never_orphans_tool_messages(self, monkeypatch):
        """驱逐边界对齐 user 消息开头——不切断 tool 配对。"""
        monkeypatch.setattr(llm, "_make_brief_summary",
                            lambda msgs, max_len=500, previous_summary=None: "测试摘要")

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
        monkeypatch.setattr(llm, "_make_brief_summary",
                            lambda msgs, max_len=500, previous_summary=None: "测试摘要")
        monkeypatch.setattr(llm, "_COMPACT_THRESHOLD", 0.001)

        big = "X" * 5000
        msgs = self._make_compaction_messages(5, big)
        msgs.extend([{"role": "tool", "content": big[:100]} for _ in range(5)])
        result = llm._compact_context(msgs, "继续做")
        assert len(result) < len(msgs), f"压缩应减少消息: {len(result)} vs {len(msgs)}"
        assert any("⏪" in m.get("content", "") for m in result if m["role"] == "system")

    def test_loaded_psyche_survives_compaction(self, monkeypatch):
        """已加载 psyche（log[0] 的 _psyche_meta）撞压缩被抢救置顶，不被摘要掉=不悄悄卸载。"""
        monkeypatch.setattr(llm, "_make_brief_summary",
                            lambda msgs, max_len=500, previous_summary=None: "摘要")
        from src.cache_context import CacheContext
        log = [{"role": "system",
                "content": "【Psyche: software-development v0.5】\n认知框架核心内容……",
                "_psyche_meta": {"name": "software-development", "version": "0.5"}}]
        big = "X" * 5000
        for i in range(6):
            log.append({"role": "user", "content": f"问题{i} " + big})
            log.append({"role": "assistant", "content": f"回答{i} " + big})
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}], log_msgs=log)
        llm._compact_cache_context(ctx, "继续", force=True)
        psyches = [m for m in ctx.log if m.get("_psyche_meta")]
        assert len(psyches) == 1, "已加载 psyche 应被抢救、不被摘要吞掉"
        assert psyches[0]["_psyche_meta"]["name"] == "software-development"
        assert ctx.log[0].get("_psyche_meta"), "抢救的 psyche 应置顶（高注意力区）"
        # 摘要里不该混入 psyche 正文（psyche 不进 to_summarize）
        assert "认知框架核心内容" not in next(
            (m["content"] for m in ctx.log if "⏪" in m.get("content", "")), "")


class TestCompressToolResult:
    """_compress_tool_result：语义摘要（v2，对齐 Hermes 工具结果压缩）。"""

    def test_short_result_not_compressed(self):
        assert llm._compress_tool_result("grep_code", "hello") == "hello"

    def test_empty_not_compressed(self):
        assert llm._compress_tool_result("grep_code", "") == ""

    def test_exact_boundary(self):
        text = "x" * _THRESHOLD
        assert llm._compress_tool_result("grep_code", text) == text

    def test_barely_over_boundary_semantic_summary(self):
        text = "y" * (_THRESHOLD + 1)
        compressed = llm._compress_tool_result("grep_code", text)
        assert len(compressed) < len(text)
        assert "grep_code" in compressed

    def test_large_result_semantic_summary(self):
        text = "H" * 5000
        compressed = llm._compress_tool_result("run_command", text)
        assert len(compressed) < len(text)
        assert "run_command" in compressed

    def test_read_file_exempt_from_compression(self):
        text = "a" * 5000
        result = llm._compress_tool_result("read_file", text)
        assert result == text

    def test_read_file_lines_exempt_from_compression(self):
        text = "b" * 5000
        result = llm._compress_tool_result("read_file_lines", text)
        assert result == text

    def test_semantic_summary_shows_size(self):
        text = "i" * 10000
        compressed = llm._compress_tool_result("generic", text)
        compressed = llm._compress_tool_result("generic", text)
        assert "10,000" in compressed or "10000" in compressed

    # ── 信号保留（本次改动核心）：不再把内容整个换 stub ──

    def test_run_command_keeps_head_and_tail_signal(self):
        """run_command 大输出：开头的失败行 + 结尾的汇总行都必须活下来。"""
        noise = "\n".join(f"tests/test_x.py::case_{i} PASSED" for i in range(400))
        text = (
            "退出码: 1\n"
            "FAILED tests/test_critical.py::test_boom - AssertionError: 关键失败\n"
            + noise
            + "\n=== 1 failed, 400 passed in 12.3s ==="
        )
        assert len(text) > _THRESHOLD
        out = llm._compress_tool_result("run_command", text)
        assert len(out) < len(text)
        assert "FAILED tests/test_critical.py::test_boom" in out  # 头部失败行
        assert "1 failed, 400 passed" in out                       # 尾部汇总
        assert "退出码: 1" in out                                  # 退出码

    def test_grep_keeps_matches_not_just_count(self):
        """Grep 大结果：首尾命中的 path:line 必须保留，不是只剩字符数。"""
        hits = "\n".join(f"src/mod_{i}.py:{i}: def handler_{i}()" for i in range(300))
        assert len(hits) > _THRESHOLD
        out = llm._compress_tool_result("grep_code", hits)
        assert len(out) < len(hits)
        assert "src/mod_0.py:0: def handler_0()" in out      # 首条命中
        assert "src/mod_299.py:299: def handler_299()" in out  # 末条命中

    def test_write_file_large_is_stubbed(self):
        """write_file 大输出=回显已写入内容=噪声，stub 即可（不必保留）。"""
        text = "x" * 5000
        out = llm._compress_tool_result("write_file", text)
        assert len(out) < 100
        assert "write_file" in out
        assert "x" * 50 not in out  # 内容确实没保留
