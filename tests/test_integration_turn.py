"""交互网 (L2) — 真跑"一整轮"的多 feature 接缝，专抓单测抓不到的涌现耦合。

单测全绿、真实路径却散，是因为 bug 不在单元里、在**缝里**：preread × 长度触发、
volatile 信号在 ctx.log 上互相挤、缓存前缀被某个 feature 顺手碰坏。这里只 mock
唯一的网络接缝 `llm._invoke_llm`，prefix 用真实 AGENTS.md，按 main() 每轮管线
顺序真跑 记忆触发 → preread → run_conversation → 两审计。

网即权威：`_drive_turn` 直接调 `main.process_turn()`——与 main 的 REPL 同一个
管线定义，不是副本。改了管线两边同步，杜绝"测试对着旧副本继续绿、真实行为已变"
的 drift。
"""
from __future__ import annotations

import json

import pytest

import src.llm as llm
import src.main as main
from src.cache_context import CacheContext

pytestmark = pytest.mark.slow


def _prefix_ctx() -> CacheContext:
    """真实 AGENTS.md 前缀 + 空 log（镜像 main 的会话起步）。"""
def _prefix_ctx() -> CacheContext:
    """真实 _make_prefix_msgs() 前缀（含 AGENTS.md + stance 常量）。"""
    ctx = CacheContext()
    ctx.rebuild_prefix(main._make_prefix_msgs())
    return ctx


def _tool_call(name: str, args: dict) -> dict:
    return {"id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _mock_llm(monkeypatch, *rounds: tuple) -> None:
    """脚本化 _invoke_llm_eager：按序返回每个 (content, tool_calls, pre_results={})。

    兼容旧调用者只给 2-tuple → 自动补第三个空返回。
    """
    def _fix(t):
        return t if len(t) == 3 else (t[0], t[1], {})
    it = iter(_fix(r) for r in rounds)
    monkeypatch.setattr(llm, "_invoke_llm_eager", lambda *_a, **_k: next(it))


def _drive_turn(ctx: CacheContext, user_input: str) -> None:
    """跑 main 的真实每轮管线（与 REPL 同源）——网即权威，见文件头。"""
    main.process_turn(ctx, user_input, on_token=lambda _t: None,
                      on_tool=lambda *_a: None, on_progress=None, session_id="")


# ── 皇冠：多 feature 同轮，缓存前缀不得被碰坏 ──────────────────

def test_prefix_survives_multifeature_turn(monkeypatch):
    """同轮跑完 preread + 工具调用 + 审计 → 缓存前缀纹丝不动。

    任何 feature 顺手往 prefix 写、或重排 prefix，send() 的哈希校验当场抛。
    """
    ctx = _prefix_ctx()
    before_hash, before_len = ctx.prefix_hash, len(ctx.prefix)
    _mock_llm(monkeypatch,
              ("", [_tool_call("think", {"thought": "看一下"})]),
              ("看完了，参数在 src/params.py:1。", []))
    _drive_turn(ctx, "重点看 src/params.py")
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


# ── 纯追加默认:prefix 之后无 volatile system 尾(Reasonix 对齐) ──

def test_pure_append_no_system_tail_by_default(monkeypatch):
    """默认(纯追加):跑完带工具调用的一轮,send() 里 prefix 之后再无 system 消息。

    对话末尾即"输入结束位置",下轮首请求可靠命中缓存单元(见 [[ctgents-context-cache]])。
    """
    ctx = _prefix_ctx()
    n_prefix = len(ctx.prefix)
    _mock_llm(monkeypatch,
              ("", [_tool_call("think", {"thought": "决定:走方案A"})]),
              ("钉好了", []))
    _drive_turn(ctx, "做个决定")
    api = ctx.send()
    # prefix 之后没有任何 system 消息(审计/任务都不挂尾)
    after_prefix = api[n_prefix:]
    assert all(m["role"] != "system" for m in after_prefix), \
        f"prefix 之后混入了 system 消息: {[m['content'][:30] for m in after_prefix if m['role']=='system']}"
    # 最后一条是对话(非 system) → 它就是缓存的输入结束单元
    assert api[-1]["role"] != "system"


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


# ── 工作人格已从前缀迁入常驻 general psyche（迁移不叠加）──────────

def test_work_persona_migrated_out_of_prefix():
    """行为人格（认知姿态/证据分层/出活准则/语气）从 AGENTS.md 前缀迁出。

    实验（2026-06-24）：测"通用姿态写成人格 vs 写成前缀规则"。迁移不叠加——
    AGENTS.md 不再含这套 stance 散文（避免两份竞争），改由常驻 general psyche 承载。
    """
    agents = main._make_agents_message()["content"]
    assert "不替 plausible" not in agents, "工作人格 stance 应已迁出 AGENTS.md 前缀"


def test_work_persona_injected_by_base_psyche():
    """工作人格由 general psyche 常驻注入 → send() 中含证据分层提醒。"""
    from src.psyche_bridge import ensure_base_psyche
    ctx = _prefix_ctx()
    ensure_base_psyche(ctx)
    api = ctx.send()
    api_text = "\n".join(m.get("content") or "" for m in api if m["role"] == "system")
    assert "证据" in api_text, "工作人格（证据分层）应由 general psyche 注入、在 send() 中"


# 建任务建议(maybe_suggest_task_nudge 挂尾)随挂尾机制整体删除,对应的
# test_substantial_work_no_task_suggests / test_short_turn_no_suggest 已移除。
# 逻辑保留为 dormant,回归须按 append-only 重做。见 [[ctgents-context-cache]]。
