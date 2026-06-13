"""_finalize_session 收尾管线测试 — 会话关闭的记忆闭环。

_finalize_session 串联四个子步骤：会话落盘 → 被动反思 → L1摘要 → 记忆收割 → 钉板转存。
每个子步骤被 except Exception 包裹——改坏任何一个，只有 logger.warning，
测试不红。这里 mock 所有子步骤，验证调用链和故障隔离。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.lesson as lesson
import src.main as main
import src.session_pins as sp
import src.session_summary as ssum
import src.tracker as tracker
from src.cache_context import CacheContext


def _ctx_with_assistant() -> CacheContext:
    """含一条 assistant 回复的上下文。"""
    ctx = CacheContext()
    ctx.log.append({"role": "user", "content": "hi"})
    ctx.log.append({"role": "assistant", "content": "hello"})
    return ctx


def _ctx_empty() -> CacheContext:
    """空上下文。"""
    return CacheContext()


# ═══════════════════════════════════════════════════════════════
# 四个子步骤全部调用
# ═══════════════════════════════════════════════════════════════


def test_all_four_substeps_called(monkeypatch):
    """含 assistant 消息的会话 → save + reflect + summary + lessons + pins 全调。"""
    calls = []

    def fake_save(messages, sid):
        calls.append("save")
        return "test-session-id"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_summary(messages, sid):
        calls.append("summary")
        return "summary.md"

    def fake_extract(messages):
        calls.append("extract")
        return []

    def fake_promote():
        calls.append("promote")
        return 0

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", fake_promote)

    ctx = _ctx_with_assistant()
    lines = main._finalize_session(ctx, None)

    assert "save" in calls
    assert "reflect" in calls
    assert "summary" in calls
    assert "extract" in calls
    assert "promote" in calls
    assert any("退出" in ln for ln in lines)


# ═══════════════════════════════════════════════════════════════
# 空会话不保存/不反思/不摘要
# ═══════════════════════════════════════════════════════════════


def test_empty_session_skips_save_reflect_summary(monkeypatch):
    """无 assistant 消息 → 不保存、不反思、不摘要（避免空文件/无效反思）。"""
    calls = []

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_summary(messages, sid):
        calls.append("summary")
        return ""

    def fake_extract(messages):
        calls.append("extract")
        return []

    def fake_promote():
        calls.append("promote")
        return 0

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", fake_promote)

    ctx = _ctx_empty()
    main._finalize_session(ctx, None)

    assert "save" not in calls
    assert "reflect" not in calls
    assert "summary" not in calls
    assert "extract" in calls, "记忆收割不受 assistant 存在条件约束"
    assert "promote" in calls, "钉板转存不受 assistant 存在条件约束"


# ═══════════════════════════════════════════════════════════════
# 故障隔离：一个子步骤抛异常不阻断后续
# ═══════════════════════════════════════════════════════════════


def test_reflect_failure_does_not_block_summary(monkeypatch):
    """reflect_on_session 抛异常 → summary 仍被调用。"""
    calls = []

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        raise RuntimeError("reflect crash")

    def fake_summary(messages, sid):
        calls.append("summary")
        return "summary.md"

    def fake_extract(messages):
        calls.append("extract")
        return []

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", lambda: 0)

    ctx = _ctx_with_assistant()
    main._finalize_session(ctx, None)

    assert "reflect" in calls
    assert "summary" in calls, "reflect 抛异常不能阻断 summary"
    assert "extract" in calls, "reflect 抛异常不能阻断记忆收割"


def test_summary_failure_does_not_block_lessons(monkeypatch):
    """write_session_summary 抛异常 → extract_lessons 仍被调用。"""
    calls = []

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_summary(messages, sid):
        calls.append("summary")
        raise RuntimeError("summary crash")

    def fake_extract(messages):
        calls.append("extract")
        return []

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", lambda: 0)

    ctx = _ctx_with_assistant()
    main._finalize_session(ctx, None)

    assert "summary" in calls
    assert "extract" in calls, "summary 抛异常不能阻断记忆收割"


def test_lessons_failure_does_not_block_pins(monkeypatch):
    """extract_lessons 抛异常 → promote_durable 仍被调用。"""
    calls = []

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_summary(messages, sid):
        calls.append("summary")
        return "summary.md"

    def fake_extract(messages):
        calls.append("extract")
        raise RuntimeError("extract crash")

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(sp, "promote_durable", lambda: calls.append("promote"))

    ctx = _ctx_with_assistant()
    main._finalize_session(ctx, None)

    assert "extract" in calls
    assert "promote" in calls, "extract 抛异常不能阻断钉板转存"


# ═══════════════════════════════════════════════════════════════
# 有 lessons 时调用 save_lessons
# ═══════════════════════════════════════════════════════════════


def test_lessons_saved_when_found(monkeypatch):
    """extract_lessons 返回非空 → save_lessons 被调用且计入返回行。"""
    saved_count = []

    def fake_save(messages, sid):
        return "sid"

    def fake_reflect(sid):
        return None

    def fake_summary(messages, sid):
        return "summary.md"

    def fake_extract(messages):
        return [{"fingerprint": "test", "content": "test lesson"}]

    def fake_save_lessons(lessons):
        saved_count.append(len(lessons))

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(ssum, "write_session_summary", fake_summary)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", fake_save_lessons)
    monkeypatch.setattr(sp, "promote_durable", lambda: 0)

    ctx = _ctx_with_assistant()
    lines = main._finalize_session(ctx, None)

    assert saved_count == [1]
    assert any("收割" in ln for ln in lines)
