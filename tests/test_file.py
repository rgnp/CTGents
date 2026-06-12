"""file.py 关键路径测试 — 备份、校验、读写、受保护文件检查。"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.file import (
    _backup,
    _find_affected_files,
    _get_changed_files,
    _invalidate_pyc,
    _list_backups,
    _read_cached,
    _resolve,
    _track_changes,
    _validate_py,
    count_lines,
    edit_file_lines,
    list_files,
    read_file,
    write_file,
)


class TestBackup:
    """备份机制测试。"""

    def test_backup_creates_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1", encoding="utf-8")
        bp = _backup(f)
        assert bp.exists()
        assert bp.read_text(encoding="utf-8") == "x = 1"

    def test_backup_creates_unique_paths(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("a", encoding="utf-8")
        f2.write_text("b", encoding="utf-8")
        bp1 = _backup(f1)
        bp2 = _backup(f2)
        assert bp1 != bp2

    def test_backup_and_list(self, tmp_path):
        """备份后可被 _list_backups 发现。"""
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            f = tmp_path / "test_backup.py"
            f.write_text("hello", encoding="utf-8")
            bp = _backup(f)
            assert bp.exists()
            backups = _list_backups(f)
            assert len(backups) >= 1
            assert backups[0] == bp
        finally:
            os.chdir(old_cwd)


class TestValidatePy:
    """语法校验测试。"""

    def test_valid_python_passes(self, tmp_path):
        f = tmp_path / "good.py"
        f.write_text("def hello():\n    return 42\n", encoding="utf-8")
        err = _validate_py(f, None)
        assert err is None

    def test_syntax_error_rollback(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("x = 1\n", encoding="utf-8")
        backup = _backup(f)
        f.write_text("def broken(:\n    pass\n", encoding="utf-8")
        err = _validate_py(f, backup)
        assert err is not None
        assert "SyntaxError" in err or "语法" in err
        assert f.read_text(encoding="utf-8") == "x = 1\n"

    def test_non_python_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# hello", encoding="utf-8")
        err = _validate_py(f, None)
        assert err is None

    def test_syntax_error_no_backup_new_file(self, tmp_path):
        """新建文件语法错，无备份时直接删除。"""
        f = tmp_path / "new_bad.py"
        f.write_text("def broken(:\n    pass\n", encoding="utf-8")
        err = _validate_py(f, None)
        assert err is not None
        assert not f.exists()


class TestReadWrite:
    """读写测试。"""

    def test_read_file_existing(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hello')", encoding="utf-8")
        result = read_file(str(f))
        assert "print('hello')" in result

    def test_read_file_nonexistent(self):
        result = read_file("/nonexistent/path.txt")
        assert "不存在" in result

    def test_read_file_binary(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        result = read_file(str(f))
        assert "无法" in result or "二进制" in result

    def test_read_file_with_line_range(self, tmp_path):
        f = tmp_path / "lines.py"
        f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
        result = read_file(str(f), start_line=2, end_line=4)
        assert "2|" in result
        assert "b" in result
        assert "d" in result

    def test_read_file_line_range_out_of_bounds(self, tmp_path):
        f = tmp_path / "lines.py"
        f.write_text("a\nb\n", encoding="utf-8")
        result = read_file(str(f), start_line=5, end_line=10)
        assert "超出" in result

    def test_read_file_line_range_inverted(self, tmp_path):
        f = tmp_path / "lines.py"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        result = read_file(str(f), start_line=3, end_line=1)
        assert "大于" in result

    def test_write_and_read_back(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "new_file.py"
        result = write_file(str(f), "answer = 42\n")
        assert "已写入" in result
        content = read_file(str(f))
        assert "answer = 42" in content

    def test_write_to_new_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "subdir" / "nested.py"
        result = write_file(str(f), "# nested\n")
        assert "已写入" in result
        assert f.exists()

    def test_write_new_syntax_error_rejected_and_removed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "broken.py"
        result = write_file(str(f), "def broken(:\n    pass\n")
        assert "已写入" not in result
        assert "语法" in result or "SyntaxError" in result
        assert not f.exists(), "语法错误的新文件应被删除"

    def test_overwrite_syntax_error_rolls_back(self, tmp_path, monkeypatch):
        """覆写已有 .py 引入语法错 → 还原原内容。"""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "ok.py"
        write_file(str(f), "good = 1\n")
        result = write_file(str(f), "def broken(:\n")
        assert "已写入" not in result
        assert f.read_text(encoding="utf-8") == "good = 1\n", "应回滚到原内容"


class TestEditFileLines:
    """行级编辑：成功路径 + 语法错自动回滚。"""

    def test_edit_replace_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        result = edit_file_lines(str(f), "replace", 2, 2, "b = 20")
        assert "已编辑" in result
        assert f.read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"

    def test_edit_insert_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\nc = 3\n", encoding="utf-8")
        edit_file_lines(str(f), "insert", 1, None, "b = 2")
        assert f.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"

    def test_edit_delete_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        edit_file_lines(str(f), "delete", 2, 2)
        assert f.read_text(encoding="utf-8") == "a = 1\nc = 3\n"

    def test_edit_out_of_bounds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\n", encoding="utf-8")
        result = edit_file_lines(str(f), "replace", 10, 10, "x")
        assert "超出" in result

    def test_edit_missing_end_line(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\n", encoding="utf-8")
        result = edit_file_lines(str(f), "replace", 1, None, "x")
        assert "需要指定" in result or "end_line" in result

    def test_edit_missing_new_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        f.write_text("a = 1\n", encoding="utf-8")
        result = edit_file_lines(str(f), "replace", 1, 1, None)
        assert "需要" in result

    def test_edit_syntax_error_rolls_back(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "m.py"
        original = "def f():\n    return 1\n"
        f.write_text(original, encoding="utf-8")
        result = edit_file_lines(str(f), "replace", 1, 1, "def f(:")
        assert "已编辑" not in result
        assert "语法" in result or "SyntaxError" in result
        assert f.read_text(encoding="utf-8") == original, "应回滚到编辑前"


class TestResolve:
    """路径解析测试。"""

    def test_relative_path(self):
        p = _resolve("src/main.py")
        assert p.exists()

    def test_current_dir_dot(self):
        p = _resolve("./src/main.py")
        assert p.exists()

    def test_nonexistent_path(self):
        p = _resolve("nonexistent_xyz.file")
        assert not p.exists()


class TestReadCached:
    """文件缓存测试。"""

    def test_cache_hit(self, tmp_path):
        import src.tools.file as fmod
        fmod._file_cache.clear()

        f = tmp_path / "cached.py"
        f.write_text("cached content", encoding="utf-8")
        r1 = _read_cached(f)
        r2 = _read_cached(f)
        assert r1 == r2 == "cached content"

    def test_cache_mtime_invalidation(self, tmp_path):
        import time

        import src.tools.file as fmod
        fmod._file_cache.clear()

        f = tmp_path / "changing.py"
        f.write_text("v1", encoding="utf-8")
        r1 = _read_cached(f)
        time.sleep(0.01)
        f.write_text("v2", encoding="utf-8")
        r2 = _read_cached(f)
        assert r1 == "v1"
        assert r2 == "v2"


class TestCountLines:
    """行数统计测试。"""

    def test_count_lines(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = count_lines(str(f))
        assert "行数" in result
        assert "3" in result or "行数: 3" in result

    def test_count_lines_nonexistent(self):
        """count_lines 对不存在的文件抛异常。"""
        with pytest.raises(FileNotFoundError):
            count_lines("/nonexistent.txt")


class TestListFiles:
    """目录列表测试。"""

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / "sub").mkdir()
        result = list_files(str(tmp_path))
        assert "a.txt" in result
        assert "b.txt" in result
        assert "sub" in result

    def test_list_files_nonexistent(self):
        result = list_files("/nonexistent_dir_xyz")
        assert "不存在" in result

    def test_list_files_cache(self, tmp_path):
        """缓存命中。"""
        r1 = list_files(str(tmp_path))
        r2 = list_files(str(tmp_path))
        assert r1 == r2


class TestInvalidatePyc:
    """字节码缓存清理测试。"""

    def test_invalidate_pyc(self, tmp_path):
        pyc_dir = tmp_path / "__pycache__"
        pyc_dir.mkdir()
        (pyc_dir / "test.cpython-312.pyc").write_text("")
        f = tmp_path / "test.py"
        f.write_text("x=1", encoding="utf-8")
        _invalidate_pyc(f)
        assert not list(pyc_dir.iterdir())


class TestTrackChanges:
    """变更追踪测试。"""

    def test_get_changed_files(self, monkeypatch):
        """_get_changed_files 至少返回一个列表。"""
        result = _get_changed_files()
        assert isinstance(result, list)

    def test_find_affected_files(self, tmp_path, monkeypatch):
        """在项目文档中搜索受影响的文件。"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "README.md").write_text("This project uses main.py\n", encoding="utf-8")
        affected = _find_affected_files("src/main.py")
        assert "README.md" in affected

    def test_track_changes(self, tmp_path, monkeypatch):
        """_track_changes 返回空或包含变更信息。"""
        monkeypatch.chdir(tmp_path)
        result = _track_changes("test.py")
        assert isinstance(result, str)


if __name__ == "__main__":
    import inspect

    tests = []
    for cls in [TestBackup, TestValidatePy, TestEditFileLines,
                TestReadWrite, TestResolve, TestReadCached,
                TestCountLines, TestListFiles, TestInvalidatePyc,
                TestTrackChanges]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(instance, name)))

    passed = 0
    for name, fn in tests:
        try:
            sig = inspect.signature(fn)
            if "tmp_path" in str(sig):
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
                fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            import traceback
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
    if passed < len(tests):
        sys.exit(1)
