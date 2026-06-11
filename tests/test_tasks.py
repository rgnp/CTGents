"""长任务状态测试：current.md 判活/注入/归档/清空 + /task 命令。

全程把 tasks 路径指向 tmp_path，绝不触碰真实 tasks/current.md。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.tasks as tasks
from src.cache_context import CacheContext


@pytest.fixture(autouse=True)
def _isolate_tasks(tmp_path, monkeypatch):
    current = tmp_path / "current.md"
    archive = tmp_path / "archive"
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", current)
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", archive)
    return current, archive


_UNFINISHED = "# 长任务：抓论文\n\n## 步骤\n- [o] Step 1: 搜索 47/250\n- [ ] Step 2: 去重\n"
_DONE = "# 长任务：抓论文\n\n## 步骤\n- [x] Step 1\n- [x] Step 2\n"


def test_has_unfinished_true(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    assert tasks.has_unfinished() is True


def test_has_unfinished_false_when_all_done(_isolate_tasks):
    _isolate_tasks[0].write_text(_DONE, encoding="utf-8")
    assert tasks.has_unfinished() is False


def test_has_unfinished_false_when_missing(_isolate_tasks):
    assert tasks.has_unfinished() is False


def test_context_message_injected_when_unfinished(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    msg = tasks.make_task_context_message()
    assert msg is not None
    assert msg["_volatile"] is True
    assert msg["role"] == "system"
    assert "未完成的长任务" in msg["content"]
    assert "47/250" in msg["content"]  # 细进度随注入透出


def test_context_message_none_when_done(_isolate_tasks, monkeypatch):
    _isolate_tasks[0].write_text(_DONE, encoding="utf-8")
    # tracker 的 get_latest_reflections 会读到 stats/ 的真数据；
    # 隔离掉它，避免历史异常干扰本测试的"全完成→None"断言。
    monkeypatch.setattr(
        "src.tracker.get_latest_reflections",
        lambda limit=3: [],
    )
    assert tasks.make_task_context_message() is None

def test_archive_moves_and_clears(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    result = tasks.archive_current("ad-papers")
    assert "已归档" in result
    assert (archive).exists()
    archived = list(archive.glob("*-ad-papers.md"))
    assert len(archived) == 1
    assert "Step 1" in archived[0].read_text(encoding="utf-8")
    assert current.read_text(encoding="utf-8") == ""  # 已清空


def test_archive_derives_slug_from_title(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.archive_current()  # 不给 slug → 从标题派生
    assert len(list(archive.glob("*.md"))) == 1


def test_clear_empties_without_archive(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.clear_current()
    assert current.read_text(encoding="utf-8") == ""
    assert not archive.exists()  # 未归档


class TestTaskCommand:
    def test_view_empty(self, _isolate_tasks):
        import src.commands as cmds
        r = cmds.dispatch("/task", CacheContext(), None)
        assert "无长任务" in r.message

    def test_view_shows_content(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task", CacheContext(), None)
        assert "抓论文" in r.message

    def test_clear_subcommand(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task clear", CacheContext(), None)
        assert "已清空" in r.message
        assert _isolate_tasks[0].read_text(encoding="utf-8") == ""

    def test_archive_subcommand(self, _isolate_tasks):
        import src.commands as cmds
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        r = cmds.dispatch("/task archive ad-papers", CacheContext(), None)
        assert "已归档" in r.message
