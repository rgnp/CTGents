"""file.py 关键路径测试 — 备份、校验、读写、受保护文件检查。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.file import (
    _backup, _backup_path, _validate_py, _validate_imports,
    _read_cached, _resolve, read_file, write_file, undo_edit, _list_backups,
    BACKUP_DIR,
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
        import os, src.tools.file as fmod
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

    def test_undo_no_backup(self, tmp_path):
        f = tmp_path / "nobackup.txt"
        f.write_text("no backup", encoding="utf-8")
        result = undo_edit(str(f))
        assert "找不到备份" in result or "没有" in result


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


class TestValidateImports:
    """Import 校验测试。"""

    def test_valid_imports_pass(self, tmp_path):
        f = tmp_path / "valid_imports.py"
        f.write_text("import os\nfrom pathlib import Path\n", encoding="utf-8")
        err = _validate_imports(f, None)
        assert err is None

    def test_missing_import_rollback(self, tmp_path):
        # _validate_imports 需要文件路径含 "src"
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        f = src_dir / "bad_import.py"
        f.write_text("x = 1\n", encoding="utf-8")
        backup = _backup(f)
        f.write_text("import nonexistent_module_xyz_123\nx = 1\n", encoding="utf-8")
        err = _validate_imports(f, backup)
        assert err is not None
        assert "回滚" in err or "不存在" in err
        # 应回滚
        assert f.read_text(encoding="utf-8") == "x = 1\n"

    def test_std_lib_imports_pass(self, tmp_path):
        f = tmp_path / "stdlib.py"
        f.write_text("import json, os, sys, re, math, time\n", encoding="utf-8")
        err = _validate_imports(f, None)
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

    def test_write_syntax_error_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "broken.py"
        result = write_file(str(f), "def broken(:\n    pass\n")
        assert "失败" in result


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
    for cls in [TestBackup, TestValidatePy, TestValidateImports,
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
