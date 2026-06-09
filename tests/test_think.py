"""测试 think.py — 策略规划工具。"""

from src.tools.think import execute, think


class TestThink:
    def test_think_returns_empty(self):
        assert think("any thought") == ""

    def test_execute_think(self):
        result = execute("think", {"thought": "test"})
        assert result == ""

    def test_execute_unknown(self):
        result = execute("unknown_tool", {})
        assert result is None
