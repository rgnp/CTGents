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


def test_c10_insert_forces_reread_before_next_edit(_isolate):
    """insert/delete 改行数 → 作废已读，逼下次 edit 重读（挡叠改改残文件）。"""
    (_isolate / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    assert tg.check("read_file", {"path": "f.py"}) is None
    # 第一次 insert 放行
    assert tg.check("edit_file_lines", {"path": "f.py", "action": "insert", "start_line": 2}) is None
    # 紧接着第二次 edit（行号已失效）→ 必须先重读，被拦
    msg = tg.check("edit_file_lines", {"path": "f.py", "action": "delete", "start_line": 3})
    assert msg is not None and "C10" in msg
    # 重读后放行
    assert tg.check("read_file", {"path": "f.py"}) is None
    assert tg.check("edit_file_lines", {"path": "f.py", "action": "delete", "start_line": 3}) is None


def test_c10_replace_keeps_read_status(_isolate):
    """单行 replace 不改行数 → 不作废已读，连续 replace 不被逼重读。"""
    (_isolate / "f.py").write_text("a\nb\nc\n", encoding="utf-8")
    assert tg.check("read_file", {"path": "f.py"}) is None
    assert tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 1}) is None
    assert tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 2}) is None


def test_reset_known_rerequires_read(_isolate):
    (_isolate / "f.py").write_text("a\n", encoding="utf-8")
    tg.check("read_file", {"path": "f.py"})
    tg.reset_known()
    msg = tg.check("edit_file_lines", {"path": "f.py", "action": "replace", "start_line": 1})
    assert msg is not None and "C10" in msg


def test_unrelated_tool_passes(_isolate):
    assert tg.check("run_command", {"command": "ls"}) is None
    assert tg.check("rag_query", {"query": "x"}) is None


# ── P1/P2：run_command 边界拦危险 git 命令 ──

def test_p1_rejects_git_add_all(_isolate):
    assert "P1" in (tg.check("run_command", {"command": "git add -A"}) or "")
    assert "P1" in (tg.check("run_command", {"command": "git add --all"}) or "")
    assert "P1" in (tg.check("run_command", {"command": "git add ."}) or "")
    # 组合命令里出现也拦
    assert "P1" in (tg.check("run_command", {"command": "git add -A && git commit -m x"}) or "")


def test_p1_allows_specific_path_add(_isolate):
    assert tg.check("run_command", {"command": "git add src/main.py"}) is None
    assert tg.check("run_command", {"command": "git add ./src/foo.py tests/bar.py"}) is None


def test_p2_rejects_force_push_main(_isolate):
    assert "P2" in (tg.check("run_command", {"command": "git push --force origin main"}) or "")
    assert "P2" in (tg.check("run_command", {"command": "git push origin master --force"}) or "")
    assert "P2" in (tg.check("run_command", {"command": "git push --force-with-lease origin main"}) or "")


def test_p2_allows_force_push_feature_and_normal_push(_isolate):
    assert tg.check("run_command", {"command": "git push -f origin feature-x"}) is None
    assert tg.check("run_command", {"command": "git push origin main"}) is None


def test_run_command_without_command_passes(_isolate):
    assert tg.check("run_command", {}) is None
