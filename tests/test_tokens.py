"""测试 tokens.py — token 估算、截断逻辑。"""

from src.tools.tokens import (
    _count_tool_calls_tokens,
    count_messages_tokens,
    estimate_tokens,
    truncate_to_budget,
)


class TestEstimateTokens:
    def test_english_text(self):
        assert estimate_tokens("hello world") > 0

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_chinese_text(self):
        assert estimate_tokens("你好世界") > 0

    def test_cjk_rate_higher_than_ascii(self):
        """同字符数下中文估得比英文多（0.6/字 vs 0.3/字符）。"""
        assert estimate_tokens("你好世界你好世界") > estimate_tokens("abcdefgh")

    def test_split_rates(self):
        """分类粗估：10 个中文字 ×0.6 + 10 个 ASCII ×0.3 = 9。"""
        assert estimate_tokens("汉" * 10 + "a" * 10) == 9


class TestCountToolCallsTokens:
    def test_empty_list(self):
        assert _count_tool_calls_tokens([]) == 0

    def test_single_tool_call(self):
        tc = [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path":"x.py"}'}}]
        result = _count_tool_calls_tokens(tc)
        assert result > 0

    def test_multiple_tool_calls(self):
        tcs = [
            {"id": "call_1", "function": {"name": "grep_code", "arguments": '{"pattern":"x"}'}},
            {"id": "call_2", "function": {"name": "read_file", "arguments": '{"path":"y.py"}'}},
        ]
        result = _count_tool_calls_tokens(tcs)
        single = _count_tool_calls_tokens([tcs[0]])
        assert result > single


class TestCountMessagesTokens:
    def test_empty_messages(self):
        assert count_messages_tokens([]) == 0

    def test_simple_text_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert count_messages_tokens(msgs) > 0

    def test_message_with_tool_calls(self):
        msgs = [{
            "role": "assistant",
            "content": "Let me read that file.",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "read_file", "arguments": '{"path":"x.py"}'},
            }],
        }]
        result = count_messages_tokens(msgs)
        plain = count_messages_tokens([{"role": "assistant", "content": "Let me read that file."}])
        assert result > plain

    def test_mixed_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!", "tool_calls": [
                {"id": "c1", "function": {"name": "think", "arguments": "{}"}},
            ]},
        ]
        result = count_messages_tokens(msgs)
        assert result > 0


class TestTruncateToBudget:
    def test_short_text_no_truncation(self):
        result = truncate_to_budget("hello", [])
        assert result == "hello"

    def test_empty_text(self):
        result = truncate_to_budget("", [])
        assert result == ""

    def test_full_context_returns_warning(self, monkeypatch):
        monkeypatch.setattr("src.tools.tokens.MAX_CONTEXT_TOKENS", 100)
        huge_msg = [{"role": "user", "content": "x" * 500}]
        result = truncate_to_budget("some text", huge_msg)
        assert "上下文已满" in result

    def test_long_text_truncated(self, monkeypatch):
        monkeypatch.setattr("src.tools.tokens.MAX_CONTEXT_TOKENS", 1000)
        long_text = "A" * 5000
        msgs = [{"role": "user", "content": "hello"}]
        result = truncate_to_budget(long_text, msgs)
        assert len(result) < len(long_text)
        assert "token 预算截断" in result
