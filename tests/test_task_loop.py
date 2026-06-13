"""测试 task_loop.py：长任务自主续跑——agent 推进 current.md 才续，停由它判断。

设计回归（见对话 2026-06-13）：续跑条件是 agent【自己推进了】current.md，不是
"任务还有未完成步骤"（旧补丁会逼出自问自答）。停由 agent 判断：停止推进 / 标 [!]
/ 全 [x]。这里用假 drive_turn（按脚本改 current.md）隔离测循环逻辑，不起 LLM。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.tasks as tasks
from src.task_loop import made_task_progress, run_task_continuation

_BASE = "# 目标锚点\nX\n\n- [x] S1\n- [ ] S2\n"


def _set_current(monkeypatch, tmp_path, text):
    task = tmp_path / "current.md"
    task.write_text(text, encoding="utf-8")
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", task)
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", tmp_path / "archive")
    return task


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


def test_continuation_stops_when_turn_makes_no_progress(monkeypatch, tmp_path):
    """agent 这步没改 current.md（写了段话/问了问题）→ 停下交还，不硬推。"""
    _set_current(monkeypatch, tmp_path, _BASE)
    drives = []
    status = []
    run_task_continuation(object(), lambda *_: drives.append(1), on_status=status.append)
    assert drives == [1], "驱动一次发现没推进就停"
    assert any("没有推进" in s or "停下" in s for s in status)


def test_continuation_budget_caps_runaway(monkeypatch, tmp_path):
    """agent 一直推进但永不完成 → 预算兜底封顶。"""
    task = _set_current(monkeypatch, tmp_path, "# 目标锚点\nX\n\n- [ ] S\n")
    n = [0]

    def drive(_c, _text):
        n[0] += 1
        task.write_text(f"# 目标锚点\nX\n\n- [o] S 进度{n[0]}\n", encoding="utf-8")

    status = []
    run_task_continuation(object(), drive, on_status=status.append, budget=3)
    assert n[0] == 3, "应到预算上限即停"
    assert any("预算" in s for s in status)
