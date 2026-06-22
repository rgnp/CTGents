"""_finalize_session 收尾管线测试 — 会话关闭的记忆闭环。

_finalize_session 串联子步骤：会话落盘 → 被动反思 → 记忆收割 → 钉板转存。
每个子步骤被 except Exception 包裹——改坏任何一个，只有 logger.warning，
测试不红。这里 mock 所有子步骤，验证调用链和故障隔离。
"""

import pytest

import src.lesson as lesson
import src.main as main
import src.session_pins as sp
import src.tracker as tracker
from src.cache_context import CacheContext


@pytest.fixture(autouse=True)
def _turn_ran():
    """默认本进程跑过一轮——多数测试验证"有内容"的收尾管线。

    turn_ran=False 的"空会话/未改动加载会话"早退路径单独由 test_no_turn_* 测。
    """
    main._session_state["turn_ran"] = True
    yield
    main._session_state["turn_ran"] = False

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
# 各子步骤全部调用
# ═══════════════════════════════════════════════════════════════

def test_substeps_all_called(monkeypatch):
    """开启关闭时收割后，含 assistant 消息的会话 → save + reflect + lessons + pins 全调。"""
    calls = []
    monkeypatch.setattr(main, "_HARVEST_ON_CLOSE", True)

    def fake_save(messages, sid):
        calls.append("save")
        return "test-session-id"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_extract(messages):
        calls.append("extract")
        return []

    def fake_promote():
        calls.append("promote")
        return 0

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", fake_promote)

    ctx = _ctx_with_assistant()
    lines = main._finalize_session(ctx, None)

    assert "save" in calls
    assert "reflect" in calls
    assert "extract" in calls
    assert "promote" in calls
    assert any("退出" in ln for ln in lines)

# ═══════════════════════════════════════════════════════════════
# 空会话不保存/不反思
# ═══════════════════════════════════════════════════════════════

def test_empty_session_skips_save_reflect(monkeypatch):
    """无 assistant 消息 → 不保存、不反思（避免空文件/无效反思）。

    收割开启时，extract/promote 不受 assistant 存在条件约束。
    """
    calls = []
    monkeypatch.setattr(main, "_HARVEST_ON_CLOSE", True)

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_extract(messages):
        calls.append("extract")
        return []

    def fake_promote():
        calls.append("promote")
        return 0

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", fake_promote)

    ctx = _ctx_empty()
    main._finalize_session(ctx, None)

    assert "save" not in calls
    assert "reflect" not in calls
    assert "extract" in calls, "记忆收割不受 assistant 存在条件约束"
    assert "promote" in calls, "钉板转存不受 assistant 存在条件约束"

# ═══════════════════════════════════════════════════════════════
# 故障隔离：一个子步骤抛异常不阻断后续
# ═══════════════════════════════════════════════════════════════

def test_reflect_failure_does_not_block_lessons(monkeypatch):
    """reflect_on_session 抛异常 → extract_lessons 仍被调用（收割开启时）。"""
    calls = []
    monkeypatch.setattr(main, "_HARVEST_ON_CLOSE", True)

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        raise RuntimeError("reflect crash")

    def fake_extract(messages):
        calls.append("extract")
        return []

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", lambda: 0)

    ctx = _ctx_with_assistant()
    main._finalize_session(ctx, None)

    assert "reflect" in calls
    assert "extract" in calls, "reflect 抛异常不能阻断记忆收割"

def test_lessons_failure_does_not_block_pins(monkeypatch):
    """extract_lessons 抛异常 → promote_durable 仍被调用（收割开启时）。"""
    calls = []
    monkeypatch.setattr(main, "_HARVEST_ON_CLOSE", True)

    def fake_save(messages, sid):
        calls.append("save")
        return "sid"

    def fake_reflect(sid):
        calls.append("reflect")
        return None

    def fake_extract(messages):
        calls.append("extract")
        raise RuntimeError("extract crash")

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
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
    """extract_lessons 返回非空 → save_lessons 被调用且计入返回行（收割开启时）。"""
    saved_count = []
    monkeypatch.setattr(main, "_HARVEST_ON_CLOSE", True)

    def fake_save(messages, sid):
        return "sid"

    def fake_reflect(sid):
        return None

    def fake_extract(messages):
        return [{"fingerprint": "test", "content": "test lesson"}]

    def fake_save_lessons(lessons):
        saved_count.append(len(lessons))

    monkeypatch.setattr(main, "save_session", fake_save)
    monkeypatch.setattr(tracker, "reflect_on_session", fake_reflect)
    monkeypatch.setattr(lesson, "extract_lessons", fake_extract)
    monkeypatch.setattr(lesson, "save_lessons", fake_save_lessons)
    monkeypatch.setattr(sp, "promote_durable", lambda: 0)

    ctx = _ctx_with_assistant()
    lines = main._finalize_session(ctx, None)

    assert saved_count == [1]
    assert any("收割" in ln for ln in lines)

# ═══════════════════════════════════════════════════════════════
# 关闭时收割默认关：save/reflect/pin 照常，但不跑 lessons/档案收割
# ═══════════════════════════════════════════════════════════════

def test_harvest_off_by_default_skips_lessons(monkeypatch):
    """默认 _HARVEST_ON_CLOSE=False → save/reflect/promote 照常，extract 不调。

    收割每次关闭都全量 LLM 重写档案、churn 记忆索引致下次新建会话前缀变动，
    已默认关闭（记忆靠 agent 显式 remember 生长）。CTG_HARVEST_ON_CLOSE=1 恢复。
    """
    calls = []
    # 不设 _HARVEST_ON_CLOSE —— 用模块默认（False）
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")
    monkeypatch.setattr(tracker, "reflect_on_session", lambda s: calls.append("reflect"))
    monkeypatch.setattr(lesson, "extract_lessons", lambda m: calls.append("extract") or [])
    monkeypatch.setattr(lesson, "save_lessons", lambda _: 0)
    monkeypatch.setattr(sp, "promote_durable", lambda: calls.append("promote") or 0)

    ctx = _ctx_with_assistant()
    main._finalize_session(ctx, None)

    assert "save" in calls
    assert "reflect" in calls
    assert "promote" in calls
    assert "extract" not in calls, "收割默认关，不应调用 extract_lessons"

# ═══════════════════════════════════════════════════════════════
# 没真跑过一轮（空会话 / 加载后未改动就退出）→ 啥也不收割
# ═══════════════════════════════════════════════════════════════

def test_no_turn_skips_everything(monkeypatch):
    """turn_ran=False → 早退，save/reflect/extract/promote 全不调（不白烧 LLM）。"""
    calls = []
    monkeypatch.setattr(main, "save_session", lambda m, s: calls.append("save") or "sid")
    monkeypatch.setattr(tracker, "reflect_on_session", lambda s: calls.append("reflect"))
    monkeypatch.setattr(lesson, "extract_lessons", lambda m: calls.append("extract") or [])
    monkeypatch.setattr(sp, "promote_durable", lambda: calls.append("promote") or 0)

    main._session_state["turn_ran"] = False  # 覆盖 autouse 的 True
    ctx = _ctx_with_assistant()  # 即便上下文里有 assistant（加载来的），也不收割
    lines = main._finalize_session(ctx, None)

    assert calls == []
    assert any("退出" in ln for ln in lines)
