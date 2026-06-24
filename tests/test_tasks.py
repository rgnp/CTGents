"""长任务状态测试：current.md 判活/注入/归档/清空 + /task 命令 + 自动归档 + 目标锚点。

全程把 tasks 路径指向 tmp_path，绝不触碰真实 tasks/current.md。
"""

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

_UNFINISHED = (
    "# 长任务：抓论文\n\n"
    "# 目标锚点\n找到最新的轨迹预测论文并分析其方法论。\n\n"
    "- [o] Step 1: 搜索 47/250\n- [ ] Step 2: 去重\n"
)
_DONE = "# 长任务：抓论文\n\n# 目标锚点\n找论文。\n\n- [x] Step 1\n- [x] Step 2\n"
_HAS_RETRY = "# 出问题了\n\n# 目标锚点\n修复。\n\n- [r] Step 1: 验证失败\n"
_HAS_BLOCKED = "# 等确认\n\n# 目标锚点\n等。\n\n- [!] Step 1: 等用户确认\n"
_ANCHORED_UNFINISHED = _UNFINISHED

def test_has_unfinished_true(_isolate_tasks):
    _isolate_tasks[0].write_text(_UNFINISHED, encoding="utf-8")
    assert tasks.has_unfinished() is True


@pytest.mark.parametrize("content,desc", [
    (_DONE, "全 [x]"),
    ("__absent__", "文件不存在"),
    (_HAS_RETRY, "全 [r]"),
    (_HAS_BLOCKED, "全 [!]"),
], ids=["done", "missing", "retry", "blocked"])
def test_has_unfinished_false(content, desc, _isolate_tasks):
    if content != "__absent__":
        _isolate_tasks[0].write_text(content, encoding="utf-8")
    assert tasks.has_unfinished() is False
    assert tasks.has_unfinished() is False

class TestCreateTask:
    def test_appends_archive_step(self, _isolate_tasks):
        current, _ = _isolate_tasks
        result = tasks.create_task("# 测试\n\n# 目标锚点\n做某事。\n\n- [ ] Step 1\n")
        assert "已写入" in result
        content = current.read_text(encoding="utf-8")
        assert "归档 current.md" in content

    def test_does_not_double_append(self, _isolate_tasks):
        current, _ = _isolate_tasks
        result = tasks.create_task(
            "# 测试\n\n# 目标锚点\n做某事。\n\n"
            "- [ ] Step 1\n- [ ] 归档 current.md → tasks/archive/\n"
        )
        assert "已写入" in result
        content = current.read_text(encoding="utf-8")
        assert content.count("归档 current.md") == 1

    def test_rejects_without_anchor(self, _isolate_tasks):
        """没有 # 目标锚点 → 拒绝写入，文件不被创建。"""
        result = tasks.create_task("# 测试\n\n- [ ] Step 1\n")
        assert "拒绝" in result
        assert "# 目标锚点" in result
        assert not _isolate_tasks[0].exists()

class TestExtractAnchor:
    def test_simple_anchor(self):
        anchor = tasks._extract_anchor("# 目标锚点\n一句话目标。\n\n正文")
        assert anchor == "一句话目标。"

    def test_multiline_anchor(self):
        """锚点只取第一行非空文本（不包含后续行）。"""
        anchor = tasks._extract_anchor("# 目标锚点\n第一行。\n第二行。\n\n正文")
        assert anchor == "第一行。", "锚点只取目标锚点之后第一行非空文本"

    def test_no_anchor(self):
        assert tasks._extract_anchor("无锚点内容") == ""

    def test_anchor_stops_at_next_heading(self):
        anchor = tasks._extract_anchor("# 目标锚点\n某目标。\n## 步骤\n- [ ] 1")
        assert anchor == "某目标。"

# make_task_context_message 已删除（dormant 孤儿，2026-06-24）——锚点注入/自动归档/未完成提醒
# 的活路径分别由 _extract_anchor 单测、task_loop、resume_reminder(test_main) 覆盖。

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
