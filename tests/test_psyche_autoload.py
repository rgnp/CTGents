"""通用人格常驻：会话首轮注入 general psyche（不分领域，不卸载）。

领域 psyche 的关键词自动加载机制已删除（2026-07-01）——用户原话"我可能说一些其他的东西
他也会触发"：关键词匹配天然假阳性，且与 general-core.md §七宣称的"我不是关键词匹配器，
唯一门禁是判断深度不够"自相矛盾。领域 psyche 现在只能靠模型自己判断后手动 /psyche load。
"""
from __future__ import annotations

from src.cache_context import CacheContext
from src.psyche_bridge import (
    _BASE_PSYCHE,
    ensure_base_psyche,
    loaded_psyches_in_log,
)


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])


def test_base_persona_injected():
    """会话开局注入 general 基础人格。"""
    ctx = _ctx()
    note = ensure_base_psyche(ctx)
    assert note is not None and _BASE_PSYCHE in note
    assert _BASE_PSYCHE in {m["name"] for m in loaded_psyches_in_log(ctx)}


def test_base_persona_idempotent():
    """已注入 → 再调不重复。"""
    ctx = _ctx()
    ensure_base_psyche(ctx)
    before = len(ctx.log)
    assert ensure_base_psyche(ctx) is None
    assert len(ctx.log) == before


def test_base_persona_disabled_by_env(monkeypatch):
    """CTG_BASE_PSYCHE=0 → 不注入。"""
    monkeypatch.setenv("CTG_BASE_PSYCHE", "0")
    ctx = _ctx()
    assert ensure_base_psyche(ctx) is None
    assert loaded_psyches_in_log(ctx) == []
