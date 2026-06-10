"""记忆写入信号：正则已废弃，detect_signal 始终返回 None。

判断"该记什么"是语义问题，正则做不了——交 agent 自主判断。
本文件验证正则不再触发。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.memory import detect_signal


def test_explicit_request_no_longer_triggers():
    """显式'记住'不再被正则匹配——agent 自主判断。"""
    assert detect_signal("记住，我们项目永远不用 git add -A") is None


def test_remember_keyword_english_no_longer_triggers():
    assert detect_signal("please remember this convention") is None


def test_correction_no_longer_triggers():
    assert detect_signal("不对，应该是先写测试再实现") is None


def test_preference_no_longer_triggers():
    assert detect_signal("我习惯用 4 空格缩进") is None


def test_neutral_returns_none():
    assert detect_signal("帮我看看这个函数为什么报错") is None


def test_difficult_semantic_case_returns_none():
    """方法论纠正——真正值得记的——正则同样不触发。agent 自主判断。"""
    assert detect_signal(
        "你推荐 embedding 是错的，因为你不看自己的实际情况"
    ) is None


def test_empty_returns_none():
    assert detect_signal("") is None
    assert detect_signal(None) is None


def test_any_input_returns_none():
    """任何输入都返回 None——函数签名保留但正则逻辑已切除。"""
    assert detect_signal("不对，记住这个约定，我喜欢用 tab") is None
    assert detect_signal("hello world") is None
