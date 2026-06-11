"""测试 analyzer_tool.py — analyze_code 工具包装层。"""

import pytest

from src.tools.analyzer_tool import execute

pytestmark = pytest.mark.slow





class TestAnalyzerTool:
    def test_execute_analyze_code(self):
        result = execute("analyze_code", {"include_tests": False})
        assert result is not None
        assert isinstance(result, str)
        # 输出应包含项目分析概要
        assert len(result) > 0

    def test_execute_unknown(self):
        result = execute("unknown_tool", {})
        assert result is None
