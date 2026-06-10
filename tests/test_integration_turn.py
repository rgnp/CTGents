"""交互网 (L2) — 真跑"一整轮"的多 feature 接缝，专抓单测抓不到的涌现耦合。

单测全绿、真实路径却散，是因为 bug 不在单元里、在**缝里**：preread × 长度触发、
volatile 信号在 ctx.log 上互相挤、缓存前缀被某个 feature 顺手碰坏。这里只 mock
唯一的网络接缝 `llm._invoke_llm`，prefix 用真实 AGENTS.md，按 main() 每轮管线
顺序真跑 思考牙 → preread → run_conversation → 两审计。

网即权威：`_drive_turn` 直接调 `main.process_turn()`——与 main 的 REPL 同一个
管线定义，不是副本。改了管线两边同步，杜绝"测试对着旧副本继续绿、真实行为已变"
的 drift。
"""
from __future__ import annotations

import json

import src.llm as llm
import src.main as main
import src.session_pins as sp
from src.cache_context import CacheContext


def _prefix_ctx() -> CacheContext:
    """真实 AGENTS.md 前缀 + 空 log（镜像 main 的会话起步）。"""
    ctx = CacheContext()
    ctx.rebuild_prefix([main._make_agents_message()])
    return ctx


def _tool_call(name: str, args: dict) -> dict:
    return {"id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _mock_llm(monkeypatch, *rounds: tuple) -> None:
    """脚本化 _invoke_llm：按序返回每个 (content, tool_calls)。"""
    it = iter(rounds)
    monkeypatch.setattr(llm, "_invoke_llm", lambda *_a, **_k: next(it))


def _drive_turn(ctx: CacheContext, user_input: str) -> None:
    """跑 main 的真实每轮管线（与 REPL 同源）——网即权威，见文件头。"""
    main.process_turn(ctx, user_input, on_token=lambda _t: None,
                      on_tool=lambda *_a: None, on_progress=None, session_id="")


# ── 皇冠：多 feature 同轮，缓存前缀不得被碰坏 ──────────────────

def test_prefix_survives_multifeature_turn(monkeypatch):
    """同轮跑完 preread + 工具调用 + 审计 → 缓存前缀纹丝不动。

    任何 feature 顺手往 prefix 写、或重排 prefix，send() 的哈希校验当场抛。
    """
    sp.clear_pins()
    ctx = _prefix_ctx()
    before_hash, before_len = ctx.prefix_hash, len(ctx.prefix)
    _mock_llm(monkeypatch,
              ("", [_tool_call("think", {"thought": "看一下"})]),
              ("看完了，参数在 src/params.py:1。", []))
    try:
        _drive_turn(ctx, "重点看 src/params.py")
    finally:
        sp.clear_pins()
    ctx.send()  # 不抛 PrefixIntegrityError = 前缀完整
    assert ctx.prefix_hash == before_hash
    assert len(ctx.prefix) == before_len


# ── 回归：预读过的文件被引用，不该触发"没读过"假阳性 ──────────

def test_preread_citation_not_false_flagged(monkeypatch):
    """预读把 params.py 内容拼进 user 消息 → 引用它 grounded → 无引用审计提示。

    钉死刚修的 bug（grounding 曾漏扫 user 消息）不复发。
    """
    ctx = _prefix_ctx()
    _mock_llm(monkeypatch, ("修好了，问题在 params.py:5。", []))
    _drive_turn(ctx, "修一下 src/params.py 的 bug")
    assert not any(m.get("_citation_audit") for m in ctx.log)


# ── volatile 信号不得在 ctx.log 上堆积 ────────────────────────

def test_volatile_signals_dont_accumulate(monkeypatch):
    """Completion 审计跨轮不累积:播 stale 编辑后恒 ==1。"""
    ctx = _prefix_ctx()
    _mock_llm(monkeypatch, ("好", []), ("好", []), ("好", []))
    for _ in range(3):
        _drive_turn(ctx, "随便说点")

    # 播一条"已改 .py 但没绿测" → completion 审计每轮重算、不堆积
    ctx.log.append({"role": "tool", "tool_call_id": "e1",
                    "_tool_name": "write_file", "content": "已写入: x.py（1 字符）"})
    _mock_llm(monkeypatch, ("继续", []), ("再继续", []))
    _drive_turn(ctx, "继续")
    assert sum(1 for m in ctx.log if m.get("_completion_audit")) == 1
    _drive_turn(ctx, "再继续")
    assert sum(1 for m in ctx.log if m.get("_completion_audit")) == 1


# ── 多 feature 的 volatile 在尾部并存，互不覆盖 ───────────────

def test_features_coexist_at_tail(monkeypatch):
    """同轮: pin 后尾部钉板存在，prefix 仍是干净的 AGENTS。"""
    sp.clear_pins()
    ctx = _prefix_ctx()
    _mock_llm(monkeypatch,
              ("", [_tool_call("pin", {"content": "决定:走方案A"})]),
              ("钉好了", []))
    try:
        _drive_turn(ctx, "做个决定")
        api = ctx.send()
        tail = "\n".join(m.get("content") or "" for m in api if m["role"] == "system")
        assert sp.PINBOARD_MARKER in tail              # 钉板在尾
        assert api[0]["role"] == "system"              # 前缀仍居首
    finally:
        sp.clear_pins()


# ── send() 结构良构：tool 消息必有前序 assistant tool_call ────

def test_send_wellformed_no_orphan_tool(monkeypatch):
    """含工具调用的轮后，send() 里每条 tool 消息都有同 id 的前序 tool_call。

    log 结构一旦错位（孤儿 tool 消息），真实 API 会 400——这条在测试里拦下。
    """
    ctx = _prefix_ctx()
    _mock_llm(monkeypatch,
              ("", [_tool_call("think", {"thought": "x"})]),
              ("完成", []))
    _drive_turn(ctx, "做点事")
    api = ctx.send()
    seen: set[str] = set()
    for m in api:
        for tc in m.get("tool_calls") or []:
            seen.add(tc["id"])
        if m["role"] == "tool":
            assert m.get("tool_call_id") in seen, "孤儿 tool 消息 → API 会 400"


# ── 思考牙：每轮常驻尾部、不累积 ──────────────────────────────

def test_thinking_stance_rides_tail_once(monkeypatch):
    """每轮恒挂一句"检索是线索不是答案"的提醒在 log 尾，多轮不累积。

    同义 bullet 放 AGENTS.md 前缀实测翻不动复读（前缀离生成点太远），故这颗行为牙
    必须挂尾靠 recency。strip-then-append 失灵则逐轮累积、撑爆尾部。
    """
    ctx = _prefix_ctx()
    _mock_llm(monkeypatch, ("好", []), ("好", []))
    _drive_turn(ctx, "随便说点")
    _drive_turn(ctx, "再说点")
    assert sum(1 for m in ctx.log if m.get("_thinking_stance")) == 1
    api = ctx.send()
    tail = "\n".join(m.get("content") or "" for m in api if m["role"] == "system")
    assert "线索" in tail, "思考提醒必须出现在尾部系统块"
