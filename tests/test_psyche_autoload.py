"""通用人格常驻：会话首轮注入 general psyche（不分领域，不卸载）。

领域 psyche 的关键词自动加载机制已删除（2026-07-01）——用户原话"我可能说一些其他的东西
他也会触发"：关键词匹配天然假阳性，且与 general-core.md §七宣称的"我不是关键词匹配器，
唯一门禁是判断深度不够"自相矛盾。领域 psyche 现在只能靠模型自己判断后手动 /psyche load。
"""
from __future__ import annotations

from pathlib import Path

import src.psyche_bridge as bridge
from src.cache_context import CacheContext
from src.psyche_bridge import (
    _BASE_PSYCHE,
    catalog_status_text,
    deactivate_scope,
    ensure_base_psyche,
    inject_psyche,
    loaded_psyches_in_log,
    remove_psyche,
    resync_system_context,
)
from src.psyche_catalog import PsycheSpec


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


def _active_names(ctx: CacheContext) -> list[str]:
    return [meta.get("id") or meta["name"] for meta in loaded_psyches_in_log(ctx)]


def test_load_resolves_dependencies_atomically_in_order():
    ctx = _ctx()
    result = inject_psyche(
        ctx, "learning-method", source="agent", reason="项目理解仍停在文件列表层",
    )
    assert result.startswith("✅")
    assert _active_names(ctx) == [
        "general", "software-development", "psyche-building", "learning-method",
    ]
    assert [m["_psyche_event"]["id"] for m in ctx.log if m.get("_psyche_event")] == _active_names(ctx)
    assert ctx.psyche_stack["general"]["scope"] == "base"
    assert ctx.psyche_stack["learning-method"]["scope"] == "task"


def test_casual_chat_is_discoverable_and_loadable():
    ctx = _ctx()
    assert "casual-chat" in catalog_status_text(ctx)
    assert inject_psyche(ctx, "casual-chat", source="agent", reason="需要从任务姿态切到闲聊").startswith("✅")
    assert _active_names(ctx) == ["general", "casual-chat"]


def test_general_cannot_be_unloaded():
    ctx = _ctx()
    ensure_base_psyche(ctx)
    before = list(ctx.log)
    assert "不可卸载" in remove_psyche(ctx, "general")
    assert ctx.log == before
    assert _active_names(ctx) == ["general"]


def test_unload_appends_event_and_preserves_activation_message():
    ctx = _ctx()
    inject_psyche(ctx, "reversibility-awareness", source="agent", reason="操作可逆性需要分区")
    activation = next(m for m in ctx.log if m.get("_psyche_event", {}).get("id") == "reversibility-awareness")
    before = len(ctx.log)

    result = remove_psyche(ctx, "reversibility-awareness", source="agent")

    assert result.startswith("✅")
    assert len(ctx.log) == before + 1
    assert activation in ctx.log
    assert ctx.log[-1]["_psyche_event"]["type"] == "deactivate"
    assert _active_names(ctx) == ["general"]


def test_parent_cannot_stop_while_active_child_depends_on_it():
    ctx = _ctx()
    inject_psyche(ctx, "paper-deep-read", source="agent", reason="需要论证审计")
    assert "仍被 active Psyche 依赖" in remove_psyche(ctx, "research")


def test_resync_restores_active_stack_from_events():
    original = _ctx()
    inject_psyche(original, "paper-deep-read", source="agent", reason="需要论证审计")
    remove_psyche(original, "paper-deep-read", source="agent")
    restored = CacheContext(log_msgs=list(original.log))

    resync_system_context(restored)

    assert _active_names(restored) == ["general", "research"]


def test_deactivate_task_scope_keeps_base_and_session():
    ctx = _ctx()
    inject_psyche(ctx, "autonomous-driving", scope="session", source="user", reason="本会话研究自动驾驶")
    inject_psyche(ctx, "reversibility-awareness", source="agent", reason="当前操作需要可逆性判断")

    results = deactivate_scope(ctx, "task")

    assert any("reversibility-awareness" in result for result in results)
    assert _active_names(ctx) == ["general", "autonomous-driving"]


def test_clear_log_resets_runtime_stack():
    ctx = _ctx()
    ensure_base_psyche(ctx)
    ctx.clear_log()
    assert loaded_psyches_in_log(ctx) == []


def test_failed_load_transaction_writes_nothing(monkeypatch, tmp_path):
    missing = tmp_path / "missing-core.md"
    spec = PsycheSpec(
        id="broken",
        version="1.0",
        kind="domain",
        manifest_path=Path(tmp_path / "manifest.yaml"),
        core_path=missing,
        requires=(),
        scope_default="task",
        summary="broken",
        judgment_delta=(),
        skills=(),
        exit_checks=(),
    )
    monkeypatch.setattr(bridge, "_catalog", lambda: {"broken": spec})
    ctx = _ctx()
    before = list(ctx.log)

    result = inject_psyche(ctx, "broken", source="agent", reason="测试原子性")

    assert "零写入" in result
    assert ctx.log == before
    assert loaded_psyches_in_log(ctx) == []
