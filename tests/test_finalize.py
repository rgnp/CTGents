"""_finalize_session 收尾管线测试 — 会话关闭。

收尾现在只串两步：会话落盘 → 被动反思（lesson/用户档案/项目知识收割 + 钉板转存
已于 2026-06-23 整体删除）。每步被 except 包裹——改坏只 logger.warning、不阻断后续。
这里 mock 子步骤，验证调用链、早退与故障隔离。
"""

import pytest

import src.main as main
import src.tracker as tracker
from src.cache_context import CacheContext


@pytest.fixture(autouse=True)
def _turn_ran():
    """默认本进程跑过一轮。turn_ran=False 的早退路径单独由 test_no_turn 测。"""
    main._session_state["turn_ran"] = True
    yield
    main._session_state["turn_ran"] = False


def _ctx_with_assistant() -> CacheContext:
    ctx = CacheContext()
    ctx.log.append({"role": "user", "content": "hi"})
    ctx.log.append({"role": "assistant", "content": "hello"})
    return ctx


def _ctx_empty() -> CacheContext:
    return CacheContext()


def test_save_and_reflect_called(monkeypatch):
    """含 assistant 消息 → save + reflect 都调。"""
    calls = []
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")
    monkeypatch.setattr(tracker, "reflect_on_session", lambda s: calls.append("reflect"))

    lines = main._finalize_session(_ctx_with_assistant(), None)

    assert "save" in calls
    assert "reflect" in calls
    assert any("保存" in ln for ln in lines)


def test_empty_session_skips_save_reflect(monkeypatch):
    """无 assistant 消息 → 不保存、不反思（避免空文件/无效反思）。"""
    calls = []
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")
    monkeypatch.setattr(tracker, "reflect_on_session", lambda s: calls.append("reflect"))

    main._finalize_session(_ctx_empty(), None)

    assert "save" not in calls
    assert "reflect" not in calls


def test_reflect_failure_isolated(monkeypatch):
    """reflect_on_session 抛异常 → 不阻断收尾（只 warning，仍返回行）。"""
    calls = []
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")

    def fake_reflect(sid):
        calls.append("reflect")
        raise RuntimeError("reflect crash")

    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)

    lines = main._finalize_session(_ctx_with_assistant(), None)

    assert "save" in calls
    assert "reflect" in calls
    assert any("保存" in ln for ln in lines), "reflect 崩溃不能阻断已完成的落盘"


def test_no_turn_skips_everything(monkeypatch):
    """turn_ran=False → 早退，save/reflect 全不调（不白烧 LLM）。"""
    calls = []
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")
    monkeypatch.setattr(tracker, "reflect_on_session", lambda s: calls.append("reflect"))

    main._session_state["turn_ran"] = False  # 覆盖 autouse 的 True
    lines = main._finalize_session(_ctx_with_assistant(), None)

    assert calls == []
    assert any("退出" in ln for ln in lines)
