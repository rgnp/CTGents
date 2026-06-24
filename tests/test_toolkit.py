"""Toolkit 统一工具定义 + 新基础文件工具（move/copy/find/mkdir）。

C16：新机制/新接线即新不变量。验 schema 派生、execute 标准化、自动发现、功能正确。
"""
from __future__ import annotations

import json

from src.tools._toolkit import Toolkit


def _sample_kit():
    tk = Toolkit()

    @tk.tool(label="样例", params={"a": "整数 a", "b": "可选字符串"}, parallel_safe=True)
    def sample(a: int, b: str | None = None) -> str:
        """一句话描述。

        更多说明（不该进 description）。
        """
        return f"a={a} b={b}"

    return tk

def test_schema_derived_from_signature_and_doc():
    tk = _sample_kit()
    schema = tk.schemas[0]
    fn = schema["function"]
    assert fn["name"] == "sample"
    assert fn["description"] == "一句话描述。", "description 取 docstring 首行"
    props = fn["parameters"]["properties"]
    assert props["a"]["type"] == "integer", "int 注解 → integer"
    assert props["a"]["description"] == "整数 a"
    assert props["b"]["type"] == "string", "str|None → string（取非 None 基类型）"
    assert fn["parameters"]["required"] == ["a"], "无默认值=required，有默认值不进"
    assert schema["_meta"]["label"] == "样例"
    assert schema["_meta"]["parallel_safe"] is True

def test_execute_dispatch_and_unknown_name():
    tk = _sample_kit()
    assert tk.execute("sample", {"a": 1, "b": "x"}) == "a=1 b=x"
    assert tk.execute("sample", {"a": 2}) == "a=2 b=None"
    assert tk.execute("不存在", {}) is None, "外来名返回 None（派发链契约）"

def test_execute_ignores_extra_args():
    tk = _sample_kit()
    assert tk.execute("sample", {"a": 1, "多余": "忽略"}) == "a=1 b=None"

def test_execute_wraps_exception_as_error_json():
    tk = Toolkit()

    @tk.tool()
    def boom() -> str:
        raise ValueError("炸了")

    out = tk.execute("boom", {})
    assert json.loads(out) == {"error": "ValueError: 炸了"}, "异常统一包成 {error} JSON"

# ── 新基础工具：自动发现 + 功能 ──

def test_new_file_tools_auto_discovered():
    from src.tools import get_tools
    names = {t["function"]["name"] for t in get_tools()}
    assert {"move_file", "find_files", "make_dir"} <= names

def test_move_make_find(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # _ensure_in_workspace 用 cwd
    from src.tools import files_more as fm

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    assert "已创建目录" in fm.execute("make_dir", {"path": "sub"})
    assert "已移动" in fm.execute("move_file", {"src": "a.txt", "dst": "sub/c.txt"})
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "sub" / "c.txt").exists()

    (tmp_path / "x.py").write_text("# py", encoding="utf-8")
    out = fm.execute("find_files", {"pattern": "*.py"})
    assert "x.py" in out

def test_move_missing_src_returns_error_json(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from src.tools import files_more as fm
    out = fm.execute("move_file", {"src": "nope.txt", "dst": "y.txt"})
    assert json.loads(out)["error"].startswith("FileNotFoundError")

# ── replace_in_file：字符串匹配式编辑（去行号漂移） ──

def test_replace_in_file_unique(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from src.tools import files_more as fm
    (tmp_path / "a.txt").write_text("foo\nbar\nbaz\n", encoding="utf-8")
    out = fm.execute("replace_in_file", {"path": "a.txt", "old": "bar", "new": "BAR"})
    assert out.startswith("已编辑:")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "foo\nBAR\nbaz\n"

def test_replace_in_file_not_found_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from src.tools import files_more as fm
    (tmp_path / "a.txt").write_text("foo\n", encoding="utf-8")
    out = fm.execute("replace_in_file", {"path": "a.txt", "old": "missing", "new": "x"})
    assert "未找到" in json.loads(out)["error"]

def test_replace_in_file_ambiguous_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from src.tools import files_more as fm
    (tmp_path / "a.txt").write_text("x\nx\n", encoding="utf-8")
    out = fm.execute("replace_in_file", {"path": "a.txt", "old": "x", "new": "y"})
    assert "不唯一" in json.loads(out)["error"], "多处匹配且未 replace_all → 报错保护"

def test_replace_in_file_replace_all(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from src.tools import files_more as fm
    (tmp_path / "a.txt").write_text("x\nx\n", encoding="utf-8")
    out = fm.execute("replace_in_file",
                     {"path": "a.txt", "old": "x", "new": "y", "replace_all": True})
    assert "替换 2 处" in out
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "y\ny\n"
