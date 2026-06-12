"""测试 guard.py：is_protected 文件保护。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.guard import PROTECTED_FILES, is_protected


def test_guard_self_protected():
    """guard.py 自己在受保护列表中。"""
    guard_path = Path(__file__).parent.parent / "src" / "guard.py"
    assert is_protected(guard_path), f"guard.py 应在受保护列表中: {PROTECTED_FILES}"


def test_write_to_guard_blocked():
    """尝试修改 guard.py → 被拒绝。"""
    from src.tools.file import write_file
    guard_path = Path(__file__).parent.parent / "src" / "guard.py"
    result = write_file(str(guard_path), "# test modification")
    assert "受保护" in result, f"应拒绝修改 guard.py，实际: {result[:100]}"


def test_critical_files_protected():
    """关键基础文件在受保护列表中。"""
    for name in ("guard.py", "main.py",
                 "validate.py", "tools/__init__.py"):
        path = Path(__file__).parent.parent / "src" / name
        assert is_protected(path), f"{name} 应在受保护列表中"


def test_regular_file_not_protected():
    """普通工具文件不在受保护列表中。"""
    assert not is_protected(Path(__file__).parent.parent / "src" / "tools" / "file.py")


def test_resolution_error_returns_false():
    """路径解析异常返回 False。"""
    assert not is_protected("\0invalid")
