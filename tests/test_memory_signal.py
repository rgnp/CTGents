"""记忆写入信号探测：机械探测语义事件，写入仍由 agent 判断。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.memory import detect_signal


def test_explicit_request_triggers():
    """显式'记住'是最强信号。"""
    nudge = detect_signal("记住，我们项目永远不用 git add -A")
    assert nudge is not None
    assert "remember" in nudge


def test_remember_keyword_english():
    assert detect_signal("please remember this convention") is not None


def test_correction_triggers():
    """用户纠正 → 提示考虑记教训。"""
    nudge = detect_signal("不对，应该是先写测试再实现")
    assert nudge is not None
    assert "纠正" in nudge


def test_preference_triggers():
    """偏好/惯例陈述 → 提示考虑记 user 偏好。"""
    nudge = detect_signal("我习惯用 4 空格缩进")
    assert nudge is not None
    assert "偏好" in nudge


def test_neutral_returns_none():
    """普通提问不触发，避免唠叨。"""
    assert detect_signal("帮我看看这个函数为什么报错") is None


def test_buduijin_not_correction():
    """'不对劲'是成语(感觉蹊跷)，不是纠正，不应触发。"""
    assert detect_signal("这个测试不对劲，但我不确定原因") is None


def test_buidui_comma_still_corrects():
    """但真正的'不对，…'纠正仍要触发。"""
    nudge = detect_signal("不对，是另一个文件")
    assert nudge is not None
    assert "纠正" in nudge


def test_empty_returns_none():
    assert detect_signal("") is None
    assert detect_signal(None) is None


def test_priority_explicit_over_correction():
    """显式要求优先级高于纠正：两者并存时返回显式提示。"""
    nudge = detect_signal("不对，记住这个约定")
    assert nudge is not None
    assert "remember" in nudge  # 显式提示含 remember，纠正提示不含
