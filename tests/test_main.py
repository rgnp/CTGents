"""main.py 纯逻辑回归。

main() 是 REPL 入口，难直接测。这里只锁抽出的纯函数 _render_turn_error：
旧实现对普通 Exception 会双重报错（先 💥 再"请求失败"），现拆成互斥两支。
"""
from __future__ import annotations

import pytest

import src.main as main
from src.main import _render_turn_error


@pytest.fixture(autouse=True)
def _isolate_state_file(tmp_path, monkeypatch):
    """任何收尾测试都不得读写真实 sessions/_state.md。"""
    monkeypatch.setattr(main, "_STATE_FILE", tmp_path / "_state.md")


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


# test_mechanisms_message_covers_every_injector 随挂尾注入器（_inject_* /
# _append_volatile_context）及 _make_mechanisms_message 整体删除而移除——它索引的
# 是已删除的挂尾机制本身。审计逻辑保留为 dormant,回归须按 append-only 重做。


# ── 会话收尾的保存闸 ──

def test_finalize_session_saves_assistant_session(monkeypatch):
    """有 assistant 回复 → 以保存后的 session id 报告落盘成功。"""
    from src.cache_context import CacheContext

    monkeypatch.setattr(main, "save_session", lambda msgs, sid: "sid-123")
    main._session_state["turn_ran"] = True
    ctx = CacheContext(log_msgs=[{"role": "user", "content": "嗨"},
                                 {"role": "assistant", "content": "好"}])
    lines = main._finalize_session(ctx, None)
    assert any("会话已保存: [sid-123]" in ln for ln in lines)


def test_finalize_session_skips_empty(monkeypatch):
    """无 assistant 回复 → 不保存（空会话不落盘）。"""
    from src.cache_context import CacheContext

    monkeypatch.setattr(main, "save_session",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应保存")))
    main._session_state["turn_ran"] = True  # 测内层 any-assistant 闸（非空会话早退）
    ctx = CacheContext(log_msgs=[{"role": "user", "content": "嗨"}])
    lines = main._finalize_session(ctx, None)
    assert not any("会话已保存" in ln for ln in lines)


def test_finalize_session_skips_when_no_turn_ran(monkeypatch):
    """未运行过对话轮次时直接退出，不保存会话。"""
    from src.cache_context import CacheContext

    monkeypatch.setattr(main, "save_session",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应保存")))
    main._session_state["turn_ran"] = False
    ctx = CacheContext(log_msgs=[{"role": "assistant", "content": "旧内容"}])
    assert main._finalize_session(ctx, None) == ["退出"]


# ── 主干 run_agent_turn：对话分支 → 按 current.md 推进升级任务分支 ──


def _stub_backbone(monkeypatch, calls, *, progressed):
    """把 run_agent_turn 的外部依赖全 stub 掉，只留控制流可观察。"""
    import src.heartbeat as heartbeat
    import src.task_loop as task_loop
    import src.tasks as tasks
    # 心跳摘要消费有副作用（pending → archive），测试必须 stub 掉，不吃真摘要
    monkeypatch.setattr(heartbeat, "consume_digest", lambda: None)
    monkeypatch.setattr(main, "process_turn",
                        lambda *a, **k: calls.append("process_turn") or "")
    monkeypatch.setattr(main, "_make_display",
                        lambda: ((lambda _t: None), (lambda: False)))
    monkeypatch.setattr(main, "_start_esc_listener", lambda: None)
    monkeypatch.setattr(main, "_stop_esc_listener", lambda: None)
    monkeypatch.setattr(main, "save_session", lambda *a: "sid")
    monkeypatch.setattr(tasks, "read_current", lambda: "x")
    monkeypatch.setattr(task_loop, "made_task_progress", lambda _b, _a: progressed)
    monkeypatch.setattr(task_loop, "run_task_continuation",
                        lambda *a, **k: calls.append("task_continuation"))


def test_run_agent_turn_drives_continuation_when_progressed(monkeypatch):
    """这一轮 agent 真推进了 current.md → run_agent_turn 自主驱动任务续跑（已接回）。"""
    from src.cache_context import CacheContext
    calls: list[str] = []
    _stub_backbone(monkeypatch, calls, progressed=True)
    main.run_agent_turn(CacheContext(), "做个任务", "sid0")
    assert calls == ["process_turn", "task_continuation"]


def test_run_agent_turn_no_continuation_when_no_progress(monkeypatch):
    """这一轮没推进 current.md（普通对话）→ 只一次驱动，不触发任务续跑。"""
    from src.cache_context import CacheContext
    calls: list[str] = []
    _stub_backbone(monkeypatch, calls, progressed=False)
    main.run_agent_turn(CacheContext(), "聊个天", "sid0")
    assert calls == ["process_turn"]
    assert "task_continuation" not in calls


def test_run_agent_turn_injects_resume_reminder_once(monkeypatch):
    """会话首轮有未完成任务 → append-only 注入一次 _resume 提醒；后续轮不重复。"""
    import src.tasks as tasks
    from src.cache_context import CacheContext
    calls: list[str] = []
    _stub_backbone(monkeypatch, calls, progressed=False)
    monkeypatch.setattr(tasks, "resume_reminder", lambda: "⏸ 有未完成任务")
    main._session_state["task_reminded"] = False
    ctx = CacheContext()
    main.run_agent_turn(ctx, "hi", "sid0")
    assert sum(1 for m in ctx.log if m.get("_resume")) == 1
    main.run_agent_turn(ctx, "hi again", "sid0")
    assert sum(1 for m in ctx.log if m.get("_resume")) == 1, "首轮提醒一次，后续不重复"


def test_run_agent_turn_injects_gate_notice_once(monkeypatch):
    """会话首轮门通行证审计有发现 → append-only 注入一次 _gate_notice；后续轮不重复。"""
    import src.gate_audit as gate_audit
    from src.cache_context import CacheContext
    calls: list[str] = []
    _stub_backbone(monkeypatch, calls, progressed=False)
    monkeypatch.setattr(gate_audit, "head_gate_notice", lambda: "⚠ 该提交可能绕过了门")
    main._session_state["task_reminded"] = True       # 隔离：只走 gate 块
    main._session_state["base_psyche_ensured"] = True
    main._session_state["gate_checked"] = False
    ctx = CacheContext()
    main.run_agent_turn(ctx, "hi", "sid0")
    assert sum(1 for m in ctx.log if m.get("_gate_notice")) == 1
    main.run_agent_turn(ctx, "hi again", "sid0")
    assert sum(1 for m in ctx.log if m.get("_gate_notice")) == 1, "首轮一次，后续不重复"


def test_run_agent_turn_injects_heartbeat_digest(monkeypatch):
    """有待送心跳摘要 → append-only 注入一次；消费后（返回 None）不再注入。"""
    import src.heartbeat as heartbeat
    from src.cache_context import CacheContext
    calls: list[str] = []
    _stub_backbone(monkeypatch, calls, progressed=False)
    main._session_state["task_reminded"] = True
    main._session_state["base_psyche_ensured"] = True
    main._session_state["gate_checked"] = True
    pending = ["## 03:30\n推进了世界模型方向，写入 knowledge/wm/card.md"]
    monkeypatch.setattr(heartbeat, "consume_digest",
                        lambda: pending.pop() if pending else None)
    ctx = CacheContext()
    main.run_agent_turn(ctx, "hi", "sid0")
    digests = [m for m in ctx.log if m.get("_heartbeat_digest")]
    assert len(digests) == 1
    assert "心跳汇报" in digests[0]["content"]
    assert "knowledge/wm/card.md" in digests[0]["content"]
    assert "/heartbeat accept" in digests[0]["content"]
    main.run_agent_turn(ctx, "hi again", "sid0")
    assert sum(1 for m in ctx.log if m.get("_heartbeat_digest")) == 1, "消费后不再注入"
