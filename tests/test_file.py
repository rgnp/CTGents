"""file.py 关键路径测试 — 备份、校验、读写、受保护文件检查。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.file import (
    _backup,
    _read_cached,
    _resolve,
    _validate_py,
    edit_file_lines,
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
        # 不同文件的备份路径不同
        assert bp1 != bp2

    def test_backup_and_list(self, tmp_path):
        """备份后可被 _list_backups 发现。"""
        import os

        import src.tools.file as fmod
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            f = tmp_path / "test_backup.py"
            f.write_text("hello", encoding="utf-8")
            bp = _backup(f)
            assert bp.exists()
            backups = fmod._list_backups(f)
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
        # 应自动回滚
        assert f.read_text(encoding="utf-8") == "x = 1\n"

    def test_non_python_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# hello", encoding="utf-8")
        err = _validate_py(f, None)
        assert err is None


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
        """新建 .py 有语法错 → 拒绝并删除（无备份可回滚）。"""
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

    def test_edit_syntax_error_rolls_back(self, tmp_path, monkeypatch):
        """编辑引入语法错 → 自动回滚，文件保持原样。"""
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


if __name__ == "__main__":
    import inspect

    tests = []
    for cls in [TestBackup, TestValidatePy, TestEditFileLines,
                TestReadWrite, TestResolve, TestReadCached]:
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
