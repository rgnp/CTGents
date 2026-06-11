"""长任务状态测试：current.md 判活/注入/归档/清空 + /task 命令 + 自动归档。

全程把 tasks 路径指向 tmp_path，绝不触碰真实 tasks/current.md。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.tasks as tasks
from src.cache_context import CacheContext

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _isolate_tasks(tmp_path, monkeypatch):
    current = tmp_path / "current.md"
    archive = tmp_path / "archive"
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", current)
    ambitions = tmp_path / "ambitions.md"
    monkeypatch.setattr(tasks, "AMBITIONS_FILE", ambitions)
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", archive)
    return current, archive


_UNFINISHED = "# 长任务：抓论文\n\n## 步骤\n- [o] Step 1: 搜索 47/250\n- [ ] Step 2: 去重\n"
_DONE = "# 长任务：抓论文\n\n## 步骤\n- [x] Step 1\n- [x] Step 2\n"
_HAS_RETRY = "# 出问题了\n\n- [r] Step 1: 验证失败\n"
_HAS_BLOCKED = "# 等确认\n\n- [!] Step 1: 等用户确认\n"


def test_has_unfinished_true(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    assert tasks.has_unfinished() is True


def test_has_unfinished_false_when_all_done(_isolate_tasks):
    _isolate_tasks[0].write_text(_DONE, encoding="utf-8")
    assert tasks.has_unfinished() is False


def test_has_unfinished_false_when_missing(_isolate_tasks):
    assert tasks.has_unfinished() is False


def test_has_unfinished_false_when_all_retry(_isolate_tasks):
    """[r] 不算活跃未完成——agent 不需要自动续做。"""
    _isolate_tasks[0].write_text(_HAS_RETRY, encoding="utf-8")
    assert tasks.has_unfinished() is False


def test_has_unfinished_false_when_all_blocked(_isolate_tasks):
    """[!] 不算活跃未完成。"""
    _isolate_tasks[0].write_text(_HAS_BLOCKED, encoding="utf-8")
    assert tasks.has_unfinished() is False


class TestIsAllDone:
    def test_true_when_all_x(self, _isolate_tasks):
        _isolate_tasks[0].write_text(_DONE, encoding="utf-8")
        assert tasks.is_all_done() is True

    def test_false_when_empty(self, _isolate_tasks):
        assert tasks.is_all_done() is False

    def test_false_when_missing(self, _isolate_tasks):
        assert tasks.is_all_done() is False

    def test_false_when_has_retry(self, _isolate_tasks):
        _isolate_tasks[0].write_text(_HAS_RETRY, encoding="utf-8")
        assert tasks.is_all_done() is False

    def test_false_when_has_blocked(self, _isolate_tasks):
        _isolate_tasks[0].write_text(_HAS_BLOCKED, encoding="utf-8")
        assert tasks.is_all_done() is False

    def test_false_when_has_todo(self, _isolate_tasks):
        _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
        assert tasks.is_all_done() is False

    def test_false_when_mixed_x_and_todo(self, _isolate_tasks):
        _isolate_tasks[0].write_text("- [x] Done\n- [ ] Not done\n", encoding="utf-8")
        assert tasks.is_all_done() is False


class TestCreateTask:
    def test_appends_archive_step(self, _isolate_tasks):
        current, _ = _isolate_tasks
        tasks.create_task("# 测试\n\n- [ ] Step 1\n")
        content = current.read_text(encoding="utf-8")
        assert "归档 current.md" in content

    def test_does_not_double_append(self, _isolate_tasks):
        current, _ = _isolate_tasks
        tasks.create_task("# 测试\n\n- [ ] Step 1\n- [ ] 归档 current.md → tasks/archive/\n")
        content = current.read_text(encoding="utf-8")
        assert content.count("归档 current.md") == 1


class TestAutoArchive:
    def test_auto_archives_when_all_done(self, _isolate_tasks, monkeypatch):
        current, archive = _isolate_tasks
        current.write_text(_DONE, encoding="utf-8")
        monkeypatch.setattr(
            "src.tracker.get_latest_reflections", lambda limit=3: []
        )
        msg = tasks.make_task_context_message()
        assert msg is not None
        assert "已自动归档" in msg["content"]
        assert current.read_text(encoding="utf-8") == ""
        assert archive.exists()
        archived = list(archive.glob("*.md"))
        assert len(archived) == 1

    def test_does_not_auto_archive_when_has_retry(self, _isolate_tasks, monkeypatch):
        current, _ = _isolate_tasks
        current.write_text(_HAS_RETRY, encoding="utf-8")
        monkeypatch.setattr(
            "src.tracker.get_latest_reflections", lambda limit=3: []
        )
        msg = tasks.make_task_context_message()
        assert msg is None
        assert current.read_text(encoding="utf-8") == _HAS_RETRY


def test_context_message_injected_when_unfinished(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    msg = tasks.make_task_context_message()
    assert msg is not None
    assert msg["_volatile"] is True
    assert msg["role"] == "system"
    assert "未完成的长任务" in msg["content"]
    assert "47/250" in msg["content"]


def test_context_message_with_auto_archive_when_done(_isolate_tasks, monkeypatch):
    _isolate_tasks[0].write_text(_DONE, encoding="utf-8")
    monkeypatch.setattr(
        "src.tracker.get_latest_reflections", lambda limit=3: []
    )
    msg = tasks.make_task_context_message()
    assert msg is not None
    assert "已自动归档" in msg["content"]
    assert _isolate_tasks[0].read_text(encoding="utf-8") == ""


def test_archive_moves_and_clears(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    result = tasks.archive_current("ad-papers")
    assert "已归档" in result
    assert (archive).exists()
    archived = list(archive.glob("*-ad-papers.md"))
    assert len(archived) == 1
    assert "Step 1" in archived[0].read_text(encoding="utf-8")
    assert current.read_text(encoding="utf-8") == ""


def test_archive_derives_slug_from_title(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.archive_current()
    assert len(list(archive.glob("*.md"))) == 1


def test_clear_empties_without_archive(_isolate_tasks):
    current, archive = _isolate_tasks
    current.write_text(_UNFINISHED, encoding="utf-8")
    tasks.clear_current()
    assert current.read_text(encoding="utf-8") == ""
    assert not archive.exists()


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
