"""Psyche 自动加载：开局按触发词命中领域 psyche → 注入。

把"该加载哪个 psyche"从 inert 的前缀散文挪到会真触发的开局检测通道。
关键词来源是各 psyche 核心文件自己声明的「触发词」行（随 psyche 进化）。
"""
from __future__ import annotations

from src.cache_context import CacheContext
from src.psyche_bridge import (
    _BASE_PSYCHE,
    detect_psyche_for,
    ensure_base_psyche,
    loaded_psyches_in_log,
    maybe_autoload_psyche,
)


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])


# ── detect_psyche_for ───────────────────────────────────────────

def test_detect_matches_autonomous_driving():
    """命中 AD 触发词（自动驾驶领域问题）→ 返回 autonomous-driving。"""
    assert detect_psyche_for("世界模型现在最火，我想冲 CVPR") == "autonomous-driving"
    assert detect_psyche_for("帮我看看自动驾驶的轨迹预测方向") == "autonomous-driving"


def test_detect_case_insensitive_english():
    """英文触发词大小写不敏感。"""
    assert detect_psyche_for("which World Model subfield for CVPR?") == "autonomous-driving"


def test_detect_no_match_returns_none():
    """无关问题不命中任何 psyche。"""
    assert detect_psyche_for("帮我重构一下登录页面的 CSS") is None
    assert detect_psyche_for("") is None


# ── maybe_autoload_psyche ───────────────────────────────────────

def test_autoload_injects_on_match():
    ctx = _ctx()
    note = maybe_autoload_psyche(ctx, "世界模型最火，自动驾驶方向怎么选题")
    assert note is not None and "autonomous-driving" in note
    loaded = {m["name"] for m in loaded_psyches_in_log(ctx)}
    assert "autonomous-driving" in loaded


def test_autoload_noop_when_no_match():
    ctx = _ctx()
    assert maybe_autoload_psyche(ctx, "今天天气不错") is None
    assert loaded_psyches_in_log(ctx) == []


def test_autoload_noop_when_already_loaded():
    """已加载 → 不重复注入（幂等）。"""
    ctx = _ctx()
    maybe_autoload_psyche(ctx, "自动驾驶世界模型选题")
    before = len(ctx.log)
    assert maybe_autoload_psyche(ctx, "再聊聊自动驾驶轨迹预测") is None
    assert len(ctx.log) == before, "已加载不应再注入第二条"


def test_autoload_disabled_by_env(monkeypatch):
    """CTG_PSYCHE_AUTOLOAD=0 → 关闭自动加载。"""
    monkeypatch.setenv("CTG_PSYCHE_AUTOLOAD", "0")
    ctx = _ctx()
    assert maybe_autoload_psyche(ctx, "自动驾驶世界模型选题") is None
    assert loaded_psyches_in_log(ctx) == []


# ── ensure_base_psyche（通用人格常驻）─────────────────────────────

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


def test_base_persona_not_keyword_routed():
    """无触发词的 general → 不会被 detect_psyche_for 命中（不抢领域路由）。"""
    assert detect_psyche_for("随便聊聊今天的天气和心情") is None
