"""测试 code.py — grep_code 代码搜索工具。"""

from src.tools.code import execute, grep_code


class TestGrepCode:
    def test_find_existing_pattern(self):
        """搜索项目中一定存在的模式。"""
        result = grep_code("def test_", "tests")
        assert result
        assert "test_" in result or "未找到" in result

    def test_pattern_not_found(self):
        result = grep_code("zzNOTFOUND999xyz", "src")
        assert "未找到" in result
        """不传 path 时默认当前目录。"""
        result = grep_code("import pytest")
        assert result


class TestExecute:
    def test_execute_grep_code(self):
        result = execute("grep_code", {"pattern": "def ", "path": "tests"})
        assert result is not None

    def test_execute_unknown_tool(self):
        result = execute("unknown_tool", {})
        assert result is None
