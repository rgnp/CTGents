"""Psyche 自动加载：开局按触发词命中领域 psyche → 注入。

把"该加载哪个 psyche"从 inert 的前缀散文挪到会真触发的开局检测通道。
关键词来源是各 psyche 核心文件自己声明的「触发词」行（随 psyche 进化）。
"""
from __future__ import annotations

from src.cache_context import CacheContext
from src.psyche_bridge import (
    _BASE_PSYCHE,
    detect_psyches_for,
    ensure_base_psyche,
    loaded_psyches_in_log,
    maybe_autoload_psyche,
)


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])


# ── detect_psyches_for（多选）─────────────────────────────────────

def test_detect_no_match_returns_empty():
    """无关问题不命中任何 psyche。写代码说"方向"不会误载科研 psyche。"""
    assert detect_psyches_for("帮我重构一下登录页面的 CSS，调整布局方向") == []
    assert detect_psyches_for("") == []


# ── maybe_autoload_psyche ───────────────────────────────────────

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
    """无触发词的 general → 不会被 detect_psyches_for 命中（不抢领域路由）。"""
    assert detect_psyches_for("随便聊聊今天的天气和心情") == []
