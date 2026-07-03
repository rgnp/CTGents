"""llm.py L2 冒烟 — 真跑 run_conversation 入口路径（只 mock LLM API 接缝）。

为什么需要：单元测试从不执行这条路径，tracker 越界相对导入（from ..tracker）
正因此溜过 477 个测试、上线运行时第一句才崩。这里只替换 _invoke_llm（唯一的
网络接缝），让 run_conversation 的接线、函数内惰性导入、工具执行循环全部真实
执行——任何 import 打错点 / 接线断裂，在测试里当场红，而非等运行时撞见。

session_id="" 是有意的：tracker 的 record_tool_call 在空会话下直接 return，
所以 from .tracker import 照常执行（抓导入错误），但不落 stats 文件（无副作用）。
"""
from __future__ import annotations

import json

import pytest

import src.llm as llm
from src.cache_context import CacheContext

pytestmark = pytest.mark.slow


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "你是测试桩"}])


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _reasoning_only_stream():
    from types import SimpleNamespace

    def _delta(content=None, reasoning=None):
        return SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)

    return iter([
        SimpleNamespace(choices=[SimpleNamespace(delta=_delta(reasoning="先想一下"))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=_delta(reasoning="还是想"))], usage=None),
    ])


def test_stream_thinking_only_no_sink_uses_reasoning_as_content(monkeypatch):
    """无 on_reasoning(aux 调用)：思考-only → 思考当内容(去丑前缀)、不空屏。"""
    backend = llm.AVAILABLE_MODELS["pro"]
    monkeypatch.setattr(backend.client.chat.completions, "create",
                        lambda **k: _reasoning_only_stream())
    shown: list[str] = []
    content, tcs = backend.chat_stream([{"role": "user", "content": "hi"}], shown.append, None)
    assert tcs is None
    assert content == "先想一下还是想", "无显示通道→思考即内容，无'只输出了思考'丑前缀"
    assert "只输出了思考" not in content
    assert any("先想一下" in s for s in shown)


def test_stream_thinking_streams_to_on_reasoning(monkeypatch):
    """有 on_reasoning(前端显示通道)：思考实时流给它、正文只放干净短提示、不进 on_token 丑文。"""
    backend = llm.AVAILABLE_MODELS["pro"]
    monkeypatch.setattr(backend.client.chat.completions, "create",
                        lambda **k: _reasoning_only_stream())
    shown: list[str] = []
    think: list[str] = []
    content, tcs = backend.chat_stream(
        [{"role": "user", "content": "hi"}], shown.append, None, on_reasoning=think.append)
    assert "".join(think) == "先想一下还是想", "思考实时流到 on_reasoning"
    assert content == llm._THINKING_ONLY_NOTE and "只输出了思考" not in content


def test_reasoning_persisted_on_assistant_but_stripped_from_api(monkeypatch):
    """思考挂 assistant 消息的 _reasoning（供重启回放折叠区），但 send() 不发给 API。

    缝：_reasoning 若漏进 API payload，就违反"不回传 reasoning"铁律、且污染缓存前缀。
    这里跑 thinking-only 一轮（on_reasoning 收到思考），断言 log 里 assistant 带
    _reasoning、而 ctx.send() 产出的消息一律不含该字段。
    """
    def _final_with_reasoning(backend, messages, on_token, session_id,
                              on_reasoning=None, **_kwargs):
        if on_reasoning:
            on_reasoning("我在想")
            on_reasoning("继续想")
        return ("最终答复", [], {})

    monkeypatch.setattr(llm, "_invoke_llm_eager", _final_with_reasoning)
    ctx = _ctx()
    out = llm.run_conversation(
        ctx, "你好", on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="")
    assert out == "最终答复"
    amsg = next(m for m in ctx.log if m.get("role") == "assistant")
    assert amsg.get("_reasoning") == "我在想继续想", "思考应挂 assistant 消息供回放"
    # 关键：发给 API 的消息一律不含 _reasoning
    assert all("_reasoning" not in m for m in ctx.send()), "_reasoning 不得进 API payload"


def test_smoke_no_tool_response(monkeypatch):
    """LLM 直接给最终答复（无工具）→ run_conversation 原样返回。

    这一条就足以执行入口的全部惰性导入（reset_storm / tracker.set_session /
    reset_safe_stats / auto_select_model），tracker 那类越界导入会在此暴露。
    """
    monkeypatch.setattr(llm, "_invoke_llm_eager", lambda *a, **k: ("最终答复", [], {}))
    tools: list[tuple] = []
    ctx = _ctx()
    out = llm.run_conversation(
        ctx, "你好",
        on_token=lambda _t: None,
        on_tool=lambda name, args: tools.append((name, args)),
        session_id="",
    )
    assert out == "最终答复"
    assert tools == []  # 未触发任何工具
    assert any(m.get("role") == "user" and m["content"] == "你好" for m in ctx.log)


def test_smoke_tool_call_then_final(monkeypatch):
    """LLM 先调 think 工具、再收尾 → 真实跑通工具执行路径。

    覆盖 _tracked_execute_tool（含 708/713 的 from .tracker import
    record_tool_call）——这正是 tracker 越界导入的另两处接缝。
    """
    responses = iter([
        ("", [_tool_call("think", {"thought": "先想想"})], {}),  # 第一轮：调工具
        ("干完了", [], {}),                                       # 第二轮：收尾
    ])
    monkeypatch.setattr(llm, "_invoke_llm_eager", lambda *a, **k: next(responses))
    tools: list[tuple] = []
    ctx = _ctx()
    out = llm.run_conversation(
        ctx, "做点事",
        on_token=lambda _t: None,
        on_tool=lambda name, args: tools.append((name, args)),
        session_id="",
    )
    assert out == "干完了"
    assert ("think", {"thought": "先想想"}) in tools  # on_tool 被回调
    # 工具结果消息进了 log，证明执行循环完整跑通
    assert any(
        m.get("role") == "tool" and m.get("_tool_name") == "think" for m in ctx.log
    )


def test_smoke_user_interrupt_returns_clean(monkeypatch):
    """LLM 调用中途用户中断（UserInterruptError）→ 清理并返回中断标记，不外抛。

    覆盖 956-958 的中断分支（含 clear_interrupt 接线）——进化改动 LLM 调用
    封装时容易碰坏这条。
    """
    def _boom(*_a, **_k):
        raise llm.UserInterruptError("Esc")

    monkeypatch.setattr(llm, "_invoke_llm_eager", _boom)
    ctx = _ctx()
    out = llm.run_conversation(
        ctx, "你好",
        on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="",
    )
    assert "已中断" in out
    # 截停信号：外层 gate 与 run_task_continuation 据此停（否则中断后续跑会接着跑）。
    assert ctx.control_signal == "interrupted"


# pin/钉板子系统已整体删除（2026-06-23）：in-session 不再显示、和 remember 冗余。
# 曾经的挂尾 smoke 测试与 test_session_pins 一并移除。


def test_smoke_malformed_tool_args_handled(monkeypatch):
    """工具参数是坏 JSON → 走 _repair_json/error 分支补上 tool 结果，循环不崩。

    覆盖 972-988 的 JSON 修复/兜底接线。坏参数的工具调用也必须补一条 tool
    结果消息，否则下一轮 API 因缺 tool 消息 400。
    """
    responses = iter([
        ("", [{"id": "call_x", "type": "function",
               "function": {"name": "think", "arguments": "{坏的"}}], {}),
        ("收尾", [], {}),
    ])
    monkeypatch.setattr(llm, "_invoke_llm_eager", lambda *_a, **_k: next(responses))
    ctx = _ctx()
    out = llm.run_conversation(
        ctx, "做事",
        on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="",
    )
    assert out == "收尾"
    # 坏参数的工具调用也补上了 tool 结果消息（否则下一轮 API 会 400）
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == "call_x"
        for m in ctx.log
    )


def test_request_breaker_stops_runaway_loop(monkeypatch):
    """LLM 永远回 tool_calls → 单轮请求数熔断生效，不再无限烧钱。

    缝：docstring 曾声称"连续 3 轮无进展兜底熔断"但循环里没有任何对应逻辑，
    唯一刹车是 95% token 上限（实测一场跑了 524 个请求 / 7980 万 prompt token）。
    """
    calls = {"n": 0}

    def _always_tools(*_a, **_k):
        calls["n"] += 1
        return (None, [_tool_call("nonexistent_tool", {})], {})

    monkeypatch.setattr(llm, "_invoke_llm_eager", _always_tools)
    monkeypatch.setattr(llm, "_MAX_REQUESTS_PER_TURN", 3)
    out = llm.run_conversation(
        _ctx(), "干活",
        on_token=lambda _t: None,
        on_tool=lambda *_a: None,
        session_id="",
    )
    assert "熔断" in out
    assert calls["n"] == 3, f"应恰好发 3 次请求后熔断，实际 {calls['n']}"
