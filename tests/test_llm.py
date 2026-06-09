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

import src.llm as llm
from src.cache_context import CacheContext


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "你是测试桩"}])


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def test_smoke_no_tool_response(monkeypatch):
    """LLM 直接给最终答复（无工具）→ run_conversation 原样返回。

    这一条就足以执行入口的全部惰性导入（reset_storm / tracker.set_session /
    reset_safe_stats / auto_select_model），tracker 那类越界导入会在此暴露。
    """
    monkeypatch.setattr(llm, "_invoke_llm", lambda *a, **k: ("最终答复", []))
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
        ("", [_tool_call("think", {"thought": "先想想"})]),  # 第一轮：调工具
        ("干完了", []),                                       # 第二轮：收尾
    ])
    monkeypatch.setattr(llm, "_invoke_llm", lambda *a, **k: next(responses))
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

    monkeypatch.setattr(llm, "_invoke_llm", _boom)
    out = llm.run_conversation(
        _ctx(), "你好",
        on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="",
    )
    assert "已中断" in out


def test_smoke_pin_rides_tail(monkeypatch):
    """一轮内 agent 调 pin → 钉板消息落在 send() 的最末尾(近因高注意力区),不进 prefix。

    走真实派发(execute_tool → session_pins.add_pin) + llm.py 轮内刷新,
    验证整条接线:pin 工具 → store → 尾部系统消息 → send() 搬到 API 末尾。
    """
    import src.session_pins as sp
    sp.clear_pins()
    responses = iter([
        ("", [_tool_call("pin", {"content": "决定:走内存易失版"})]),
        ("好了", []),
    ])
    monkeypatch.setattr(llm, "_invoke_llm", lambda *_a, **_k: next(responses))
    ctx = _ctx()
    try:
        out = llm.run_conversation(
            ctx, "记住这个决定",
            on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="",
        )
        assert out == "好了"
        api = ctx.send()
        assert sp.PINBOARD_MARKER in (api[-1].get("content") or "")  # 钉在最末尾
        assert api[-1]["role"] == "system"
        assert "走内存易失版" in api[-1]["content"]
        # 不在 prefix(prefix 只有测试桩,钉板绝不破坏缓存前缀)
        assert all(sp.PINBOARD_MARKER not in (m.get("content") or "") for m in ctx.prefix)
    finally:
        sp.clear_pins()


def test_smoke_unpin_removes_tail_message(monkeypatch):
    """全部 unpin 后,尾部钉板消息被移除(不残留空钉板)。"""
    import src.session_pins as sp
    sp.clear_pins()
    sp.add_pin("旧决定")
    responses = iter([
        ("", [_tool_call("unpin", {"content": "旧决定"})]),
        ("取下了", []),
    ])
    monkeypatch.setattr(llm, "_invoke_llm", lambda *_a, **_k: next(responses))
    ctx = _ctx()
    try:
        llm.run_conversation(
            ctx, "取下那条",
            on_token=lambda _t: None, on_tool=lambda *_a: None, session_id="",
        )
        api = ctx.send()
        assert all(sp.PINBOARD_MARKER not in (m.get("content") or "") for m in api)
    finally:
        sp.clear_pins()


def test_smoke_malformed_tool_args_handled(monkeypatch):
    """工具参数是坏 JSON → 走 _repair_json/error 分支补上 tool 结果，循环不崩。

    覆盖 972-988 的 JSON 修复/兜底接线。坏参数的工具调用也必须补一条 tool
    结果消息，否则下一轮 API 因缺 tool 消息 400。
    """
    responses = iter([
        ("", [{"id": "call_x", "type": "function",
               "function": {"name": "think", "arguments": "{坏的"}}]),
        ("收尾", []),
    ])
    monkeypatch.setattr(llm, "_invoke_llm", lambda *_a, **_k: next(responses))
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
