"""测试 task_loop.py：长任务自主续跑——agent 推进 current.md 才续，停由它判断。

设计回归（见对话 2026-06-13）：续跑条件是 agent【自己推进了】current.md，不是
"任务还有未完成步骤"（旧补丁会逼出自问自答）。停由 agent 判断：停止推进 / 标 [!]
/ 全 [x]。这里用假 drive_turn（按脚本改 current.md）隔离测循环逻辑，不起 LLM。
"""

import src.tasks as tasks
from src.task_loop import made_task_progress, run_task_continuation
from src.tasks import resume_reminder

_BASE = "# 目标锚点\nX\n\n- [x] S1\n- [ ] S2\n"

def _set_current(monkeypatch, tmp_path, text):
    task = tmp_path / "current.md"
    task.write_text(text, encoding="utf-8")
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", task)
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", tmp_path / "archive")
    return task

# ── resume_reminder：跨会话/重启恢复未完成任务的"注意力" ──

def test_resume_reminder_none_when_all_done(monkeypatch, tmp_path):
    _set_current(monkeypatch, tmp_path, "# 目标锚点\nX\n\n- [x] S1\n")
    assert resume_reminder() is None

def test_resume_reminder_surfaces_unfinished_with_anchor(monkeypatch, tmp_path):
    _set_current(monkeypatch, tmp_path, _BASE)  # 含 [ ] S2
    out = resume_reminder()
    assert out is not None
    assert "未完成" in out and "S2" in out and "目标锚点" in out

# ── made_task_progress：判定"agent 这轮是否自己推进了任务" ──

def test_made_task_progress_true_on_advance():
    after = _BASE.replace("- [ ] S2", "- [o] S2 50%")
    assert made_task_progress(_BASE, after) is True

def test_made_task_progress_false_on_creation():
    """新建任务（before 空）不算推进——建计划≠决定立刻执行。"""
    assert made_task_progress("", _BASE) is False

def test_made_task_progress_false_on_no_change():
    assert made_task_progress(_BASE, _BASE) is False

def test_made_task_progress_false_on_blocker():
    """标了 [!]（要你拍板）不算"推进续跑"——该交还用户。"""
    after = _BASE.replace("- [ ] S2", "- [!] S2 等你确认")
    assert made_task_progress(_BASE, after) is False

def test_made_task_progress_false_when_all_done():
    """全 [x] 不靠这个触发续跑（由 continuation 内部归档处理）。"""
    after = "# 目标锚点\nX\n\n- [x] S1\n- [x] S2\n"
    assert made_task_progress(_BASE, after) is False

# ── run_task_continuation：自主驱动直到 agent 自己停 ──

def test_continuation_drives_until_all_done_then_archives(monkeypatch, tmp_path):
    task = _set_current(monkeypatch, tmp_path,
                        "# 目标锚点\nX\n\n- [x] S1\n- [ ] S2\n- [ ] S3\n")
    states = iter([
        "# 目标锚点\nX\n\n- [x] S1\n- [x] S2\n- [ ] S3\n",  # drive1 完成 S2
        "# 目标锚点\nX\n\n- [x] S1\n- [x] S2\n- [x] S3\n",  # drive2 完成 S3 → 全 [x]
    ])
    drives = []

    def drive(_c, _text):
        drives.append(1)
        task.write_text(next(states), encoding="utf-8")

    status = []
    run_task_continuation(object(), drive, on_status=status.append)
    assert len(drives) == 2
    assert any("完成" in s or "归档" in s for s in status)
    assert task.read_text(encoding="utf-8").strip() == "", "全 [x] 后应归档清空"

def test_continuation_stops_on_blocker_without_driving(monkeypatch, tmp_path):
    _set_current(monkeypatch, tmp_path,
                 "# 目标锚点\nX\n\n- [x] S1\n- [!] S2 用 A 还是 B?\n- [ ] S3\n")
    drives = []
    status = []
    run_task_continuation(object(), lambda *_: drives.append(1), on_status=status.append)
    assert drives == [], "有 [!] 应直接停、不驱动"
    assert any("拍板" in s or "[!]" in s for s in status)

def test_continuation_stops_on_stall_after_grace(monkeypatch, tmp_path):
    """连续没推进 current.md → 容一次宽限（忘勾标记≠卡住），到 stall_limit 才停。"""
    _set_current(monkeypatch, tmp_path, _BASE)
    drives = []
    status = []
    run_task_continuation(object(), lambda *_: drives.append(1),
                          on_status=status.append, stall_limit=2)
    assert drives == [1, 1], "stall_limit=2：第一步没推进给宽限，第二步仍没推进才停"
    assert any("没推进" in s or "卡住" in s for s in status)

def test_continuation_stall_limit_one_stops_immediately(monkeypatch, tmp_path):
    """stall_limit=1 退回旧行为：一步没推进即停。"""
    _set_current(monkeypatch, tmp_path, _BASE)
    drives = []
    run_task_continuation(object(), lambda *_: drives.append(1),
                          on_status=lambda _s: None, stall_limit=1)
    assert drives == [1]

def _ctx_with_signal(sig, payload=""):
    """假 ctx：drive 后带显式控制信号（模拟 agent 调了 task_done/need_user）。"""
    import types
    holder = types.SimpleNamespace(control_signal=None, control_payload=None)

    def drive(_c, _text):
        holder.control_signal = sig
        holder.control_payload = payload

    return holder, drive

def test_continuation_stops_on_need_user_signal(monkeypatch, tmp_path):
    """调 need_user → 续跑停下并把问题交还用户。"""
    _set_current(monkeypatch, tmp_path, _BASE)
    ctx, drive = _ctx_with_signal("need_user", "用 A 还是 B？")
    status = []
    run_task_continuation(ctx, drive, on_status=status.append)
    assert any("拍板" in s and "A 还是 B" in s for s in status)

def test_continuation_stops_on_interrupt_signal(monkeypatch, tmp_path):
    """用户 Esc 截停（control_signal=interrupted）→ 续跑立即停，不再驱动下一步。"""
    _set_current(monkeypatch, tmp_path, _BASE)
    ctx, drive = _ctx_with_signal("interrupted")
    status = []
    run_task_continuation(ctx, drive, on_status=status.append)
    assert any("截停" in s for s in status)

def test_continuation_stops_on_task_done_signal(monkeypatch, tmp_path):
    """调 task_done 且全完成 → 续跑归档并停。"""
    task = _set_current(monkeypatch, tmp_path, _BASE)

    import types
    ctx = types.SimpleNamespace(control_signal=None, control_payload=None)

    def drive(_c, _text):
        task.write_text("# 目标锚点\nX\n\n- [x] S1\n- [x] S2\n", encoding="utf-8")
        ctx.control_signal = "task_done"
        ctx.control_payload = "都做完了"

    status = []
    run_task_continuation(ctx, drive, on_status=status.append)
    assert any("完成" in s for s in status)
    assert task.read_text(encoding="utf-8").strip() == "", "task_done 且全 [x] → 归档清空"

def test_continuation_budget_caps_runaway(monkeypatch, tmp_path):
    """预算兜底：agent 一直推进但永不完成时封顶。"""
    task = _set_current(monkeypatch, tmp_path, "# 目标锚点\nX\n\n- [ ] S\n")
    n = [0]

    def drive(_c, _text):
        n[0] += 1
        task.write_text(f"# 目标锚点\nX\n\n- [o] S 进度{n[0]}\n", encoding="utf-8")

    status = []
    run_task_continuation(object(), drive, on_status=status.append, budget=3)
    assert n[0] == 3, "应到自主上限即停"
    assert any("自主上限" in s or "上限" in s for s in status)
