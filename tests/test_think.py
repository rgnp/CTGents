"""测试 think.py — 策略规划工具（返回空字符串）。"""

from src.tools.think import execute


class TestThink:
    def test_think_returns_empty(self):
        """Think 工具记录推理过程后返回空字符串（日志效果在前端，非返回值）。"""
        result = execute("think", {"thought": "any thought"})
        assert result == ""
