"""_repair_json：DeepSeek 工具调用常见 JSON 格式错误的修复。

这条路径决定工具调用可靠性，之前完全无测试。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import _repair_json


def test_python_literals_to_json():
    """True/False/None → true/false/null。"""
    assert json.loads(_repair_json('{"a": True, "b": False, "c": None}')) == {
        "a": True, "b": False, "c": None,
    }


def test_single_quotes():
    """单引号键/值 → 双引号。"""
    assert json.loads(_repair_json("{'name': 'x'}")) == {"name": "x"}


def test_trailing_comma():
    """尾逗号移除。"""
    assert json.loads(_repair_json('{"a": 1,}')) == {"a": 1}


def test_missing_close_brace():
    """缺闭合括号按计数补全。"""
    assert json.loads(_repair_json('{"a": 1')) == {"a": 1}


def test_trailing_junk_truncated():
    """有效 JSON 后的多余字符截断。"""
    assert json.loads(_repair_json('{"a": 1}garbage')) == {"a": 1}


def test_single_quote_plus_trailing_comma_combo():
    """组合错误：单引号 + 尾逗号同现。

    回归 #6：旧逻辑在去引号未单独解析成功时丢弃结果、回退原串，
    导致后续尾逗号步骤仍在单引号串上操作 → 修不好。
    """
    assert json.loads(_repair_json("{'a': 1,}")) == {"a": 1}


def test_already_valid_passthrough():
    """已合法的 JSON 原样可解析。"""
    assert json.loads(_repair_json('{"x": [1, 2, 3]}')) == {"x": [1, 2, 3]}
