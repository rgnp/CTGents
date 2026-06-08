"""工具边界拦截层测试：C10 读后写、C14 目录，机械校验不靠注意力。

project root 隔离到 tmp_path，不碰真实文件。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import src.tools.tool_guard as tg


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_project_root", lambda: tmp_path.resolve())
    tg.reset_known()
    return tmp_path


# ── C14：文件放对目录 ──

def test_c14_rejects_new_root_py(_isolate):
    msg = tg.check("write_file", {"path": "newmod.py", "content": "x"})
    assert msg is not None and "C14" in msg


def test_c14_rejects_new_root_json(_isolate):
    assert tg.check("write_file", {"path": "data.json", "content": "{}"}) is not None


def test_c14_allows_src_subdir(_isolate):
    assert tg.check("write_file", {"path": "src/newmod.py", "content": "x"}) is None


def test_c14_allows_overwrite_existing_root_file(_isolate):
    (_isolate / "existing.py").write_text("old", encoding="utf-8")
    assert tg.check("write_file", {"path": "existing.py", "content": "new"}) is None


def test_c14_allows_non_banned_ext_at_root(_isolate):
    assert tg.check("write_file", {"path": "README.md", "content": "x"}) is None


# ── C10：读后写 ──

def test_c10_rejects_edit_without_read(_isolate):
    (_isolate / "f.py").write_text("a\nb\n", encoding="utf-8")
    msg = tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 1})
    assert msg is not None and "C10" in msg


def test_c10_allows_edit_after_read(_isolate):
    (_isolate / "f.py").write_text("a\nb\n", encoding="utf-8")
    assert tg.check("read_file", {"path": "f.py"}) is None  # 登记
    assert tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 1}) is None


def test_c10_write_counts_as_seen(_isolate):
    """write_file 写过的文件，后续 edit_file_lines 放行。"""
    (_isolate / "src").mkdir()
    (_isolate / "src" / "g.py").write_text("a\n", encoding="utf-8")
    assert tg.check("write_file", {"path": "src/g.py", "content": "a\n"}) is None  # 登记
    assert tg.check("edit_file_lines", {"path": "src/g.py", "action": "replace", "start_line": 1}) is None


def test_c10_nonexistent_passes_through(_isolate):
    """不存在的文件 → 不拦，交给 edit 自己报错。"""
    assert tg.check("edit_file_lines", {"path": "nope.py", "action": "replace", "start_line": 1}) is None


def test_reset_known_rerequires_read(_isolate):
    (_isolate / "f.py").write_text("a\n", encoding="utf-8")
    tg.check("read_file", {"path": "f.py"})
    tg.reset_known()
    msg = tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 1})
    assert msg is not None and "C10" in msg


def test_unrelated_tool_passes(_isolate):
    assert tg.check("run_command", {"command": "ls"}) is None
    assert tg.check("rag_query", {"query": "x"}) is None
