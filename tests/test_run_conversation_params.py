"""run_conversation 新参数（tools/track_stats/max_requests）——透传与默认值回归锚。

默认值必须精确等于旧行为（主链路零回归）；非默认值供 delegate worker 隔离嵌套用。
"""

from types import SimpleNamespace

import pytest

import src.llm as llm
from src import tracker
from src.cache_context import CacheContext


@pytest.fixture(autouse=True)
def _quiet_globals(monkeypatch):
    """隔离全局副作用：不真选模型、不 reconcile、不动 tracker 主指针。"""
    fake_backend = SimpleNamespace(info=SimpleNamespace(name="Fake", supports_tools=True))
    monkeypatch.setattr(llm, "auto_select_model", lambda _q: fake_backend)
    monkeypatch.setattr(llm, "_reconcile_system_context", lambda _ctx: None)
    yield
    tracker.set_session("")


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "p"}])


_NOOP = lambda *_a, **_k: None  # noqa: E731


def test_default_passthrough_matches_old_behavior(monkeypatch):
    captured = {}

    def fake_eager(backend, messages, on_token, session_id, **kwargs):
        captured.update(kwargs)
        return "回答", None, {}

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    result = llm.run_conversation(_ctx(), "问题", _NOOP, _NOOP)
    assert result == "回答"
    assert captured["track_stats"] is True   # 旧行为：主会话计统计
    assert captured["tools"] is None         # 旧行为：None → get_tools()


def test_custom_tools_and_track_stats_forwarded(monkeypatch):
    captured = {}
    subset = [{"type": "function", "function": {"name": "search_web"}}]

    def fake_eager(backend, messages, on_token, session_id, **kwargs):
        captured.update(kwargs)
        return "ok", None, {}

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    llm.run_conversation(_ctx(), "q", _NOOP, _NOOP, tools=subset, track_stats=False)
    assert captured["tools"] is subset
    assert captured["track_stats"] is False


def test_max_requests_budget_breaker(monkeypatch):
    """Fake 永远返回 tool_calls → max_requests=2 时恰好 2 次后熔断停。"""
    calls = []

    def fake_eager(backend, messages, on_token, session_id, **kwargs):
        calls.append(1)
        return None, [{"function": {"name": "think", "arguments": "{}"}}], {}

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    monkeypatch.setattr(llm, "_handle_tool_results",
                        lambda *a, **k: (None, 0))
    monkeypatch.setattr(llm, "_detect_control_signal", lambda *_args: None)
    result = llm.run_conversation(_ctx(), "q", _NOOP, _NOOP, max_requests=2)
    assert len(calls) == 2
    assert "熔断上限（2）" in result


def test_none_max_requests_uses_global_default(monkeypatch):
    """max_requests 缺省 → 用全局 _MAX_REQUESTS_PER_TURN（旧行为）。"""
    calls = []

    def fake_eager(backend, messages, on_token, session_id, **kwargs):
        calls.append(1)
        if len(calls) >= 3:
            return "完成", None, {}
        return None, [{"function": {"name": "think", "arguments": "{}"}}], {}

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    monkeypatch.setattr(llm, "_handle_tool_results", lambda *a, **k: (None, 0))
    monkeypatch.setattr(llm, "_detect_control_signal", lambda *_args: None)
    monkeypatch.setattr(llm, "_MAX_REQUESTS_PER_TURN", 50)
    result = llm.run_conversation(_ctx(), "q", _NOOP, _NOOP)
    assert result == "完成"
    assert len(calls) == 3  # 没被小预算误熔断
