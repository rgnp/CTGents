"""main.py 纯逻辑回归。

main() 是 REPL 入口，难直接测。这里只锁抽出的纯函数 _render_turn_error：
旧实现对普通 Exception 会双重报错（先 💥 再"请求失败"），现拆成互斥两支。
"""
from __future__ import annotations

import src.main as main
from src.main import _make_mechanisms_message, _render_turn_error


def test_exception_friendly_and_no_break():
    lines, should_break = _render_turn_error(ValueError("boom"))
    assert should_break is False
    joined = "\n".join(lines)
    assert "💥 错误: ValueError: boom" in joined
    assert "请求失败" not in joined, "普通异常不应再走'请求失败'分支（旧双重报错）"


def test_systemexit_nonzero_breaks():
    lines, should_break = _render_turn_error(SystemExit(2))
    assert should_break is True
    joined = "\n".join(lines)
    assert "请求失败" in joined
    assert "💥" not in joined


def test_non_exception_base_no_break():
    """非 SystemExit 的 BaseException（如 GeneratorExit）→ 提示但不退出。"""
    lines, should_break = _render_turn_error(GeneratorExit())
    assert should_break is False
    assert "请求失败" in "\n".join(lines)


# ── C16 缝：派生的运行时机制索引（注入缓存前缀，治"对自身架构失忆/编造"）──

def test_mechanisms_message_covers_every_injector():
    """机制索引必须自动覆盖每个 _inject_* + _append_volatile_context。

    缝：agent 曾否认 completion_audit 存在、重造已有机制。索引内省派生 → 新增机制
    自动入册、不会漏；本测试同时守住"派生没断线"（若有人改了内省逻辑会红）。
    """
    content = _make_mechanisms_message()["content"]
    injectors = [n for n in dir(main)
                 if n.startswith("_inject_") or n == "_append_volatile_context"]
    assert injectors, "main 里应有 _inject_* 机制"
    for n in injectors:
        assert n in content, f"机制索引漏了 {n}"
    # 两个被 agent 否认过/重造过的审计机制必须在册
    assert "_inject_completion_audit" in content
    assert "_inject_citation_audit" in content


# ── C16 缝：会话收尾必须触发反思（被动进化分析层的唯一写入口） ──

def test_finalize_session_triggers_reflection(monkeypatch):
    """有 assistant 回复 → 保存 + reflect_on_session(以保存后的 id 调用)。

    缝：reflect 调用曾挂在 load_session 的 return 之后(不可达)，整层分析
    管线静默死亡数日(stats/ 零 reflection 文件)。锁住"收尾必反思"接线。
    """
    import src.tracker as tracker
    from src.cache_context import CacheContext

    calls: list[str] = []
    monkeypatch.setattr(main, "save_session", lambda msgs, sid: "sid-123")
    monkeypatch.setattr(tracker, "reflect_on_session",
                        lambda sid: calls.append(sid) or None)
    ctx = CacheContext(log_msgs=[{"role": "user", "content": "嗨"},
                                 {"role": "assistant", "content": "好"}])
    lines = main._finalize_session(ctx, None)
    assert calls == ["sid-123"], "收尾必须以保存后的 session_id 触发反思"
    assert any("会话已保存" in ln for ln in lines)


def test_finalize_session_skips_empty(monkeypatch):
    """无 assistant 回复 → 不保存也不反思（空会话不落盘）。"""
    import src.tracker as tracker
    from src.cache_context import CacheContext

    monkeypatch.setattr(main, "save_session",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应保存")))
    monkeypatch.setattr(tracker, "reflect_on_session",
                        lambda sid: (_ for _ in ()).throw(AssertionError("不应反思")))
    ctx = CacheContext(log_msgs=[{"role": "user", "content": "嗨"}])
    lines = main._finalize_session(ctx, None)
    assert not any("会话已保存" in ln for ln in lines)


def test_finalize_session_reflect_failure_not_blocking(monkeypatch):
    """反思抛异常 → 不阻塞退出，保存行仍在。"""
    import src.tracker as tracker
    from src.cache_context import CacheContext

    monkeypatch.setattr(main, "save_session", lambda msgs, sid: "sid-9")
    monkeypatch.setattr(tracker, "reflect_on_session",
                        lambda sid: (_ for _ in ()).throw(OSError("disk")))
    ctx = CacheContext(log_msgs=[{"role": "assistant", "content": "好"}])
    lines = main._finalize_session(ctx, None)
    assert any("会话已保存" in ln for ln in lines)
    assert any("退出" in ln for ln in lines)
