"""main.py 纯逻辑回归。

main() 是 REPL 入口，难直接测。这里只锁抽出的纯函数 _render_turn_error：
旧实现对普通 Exception 会双重报错（先 💥 再"请求失败"），现拆成互斥两支。
"""
from __future__ import annotations

import src.main as main
from src.main import _render_turn_error


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
    main._session_state["turn_ran"] = True
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
    main._session_state["turn_ran"] = True  # 测内层 any-assistant 闸（非空会话早退）
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
    main._session_state["turn_ran"] = True
    ctx = CacheContext(log_msgs=[{"role": "assistant", "content": "好"}])
    lines = main._finalize_session(ctx, None)
    assert any("会话已保存" in ln for ln in lines)
    assert any("退出" in ln for ln in lines)


# ── 主干 run_agent_turn：对话分支 → 按 current.md 推进升级任务分支 ──


def _stub_backbone(monkeypatch, calls, *, progressed):
    """把 run_agent_turn 的外部依赖全 stub 掉，只留控制流可观察。"""
    import src.task_loop as task_loop
    import src.tasks as tasks
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


def _audit_disp(captured):
    """最小 Display 替身，只捕获 on_status。"""
    from src import ui
    return ui.Display(
        make_display=lambda: ((lambda _t: None), (lambda: False)),
        on_tool=lambda *_a: None,
        on_status=lambda s: captured.append(s),
        on_footer=lambda *_a: None,
        end_message=lambda *_a: None,
    )


def _write_without_read_log():
    """构造一段触发审计的 log：本轮 write_file 了一个 .py 但没先 read_file。"""
    return [
        {"role": "user", "content": "改个文件"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "已写入: x.py", "_tool_name": "write_file"},
        {"role": "assistant", "content": "搞定了"},
    ]


def test_post_turn_audits_append_only_and_print():
    """④审计命中 → 作为非 volatile _audit system 消息 append 进 log + 打印给用户。"""
    from src.cache_context import CacheContext
    ctx = CacheContext(log_msgs=_write_without_read_log())
    captured: list[str] = []
    main._run_post_turn_audits(ctx, _audit_disp(captured))
    audit_msgs = [m for m in ctx.log if m.get("_audit")]
    assert audit_msgs, "审计 nudge 应已 append 进 log"
    assert all(m["role"] == "system" and not m.get("_volatile") for m in audit_msgs)
    assert captured, "审计 nudge 应打印给用户"


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


def test_post_turn_audits_dedup_no_repeat():
    """同一条 nudge 条件不变 → 第二次审计不重复追加（去重防刷屏）。"""
    from src.cache_context import CacheContext
    ctx = CacheContext(log_msgs=_write_without_read_log())
    main._run_post_turn_audits(ctx, _audit_disp([]))
    n_after_first = sum(1 for m in ctx.log if m.get("_audit"))
    main._run_post_turn_audits(ctx, _audit_disp([]))
    n_after_second = sum(1 for m in ctx.log if m.get("_audit"))
    assert n_after_first == n_after_second
