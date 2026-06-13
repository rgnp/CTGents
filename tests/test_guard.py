"""测试 guard.py：三层自我修改分级（不可变核 / 核心业务走安全带 / 自由）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.guard import (
    IMMUTABLE_FILES,
    PROTECTED_FILES,
    is_core,
    is_immutable,
    is_protected,
)

_SRC = Path(__file__).parent.parent / "src"


def test_immutable_safety_core():
    """强制安全的机制本身=不可变核（改了等于让防护失效）。"""
    for name in ("guard.py", "tool_guard.py", "gate_audit.py"):
        assert is_immutable(_SRC / name), f"{name} 应是不可变核"


def test_core_business_files_are_core_not_immutable():
    """核心业务文件可改（非不可变），但归 is_core → 走安全带。"""
    for name in ("main.py", "commands.py", "validate.py", "tools/__init__.py"):
        p = _SRC / name
        assert not is_immutable(p), f"{name} 应可改（非不可变核）"
        assert is_core(p), f"{name} 应归核心业务（走 import 冒烟安全带）"


def test_immutable_is_not_also_core():
    """不可变核不重复归核心业务。"""
    assert not is_core(_SRC / "guard.py")


def test_leaf_file_free():
    """普通工具文件既非不可变也非核心，自由改。"""
    p = _SRC / "tools" / "file.py"
    assert not is_immutable(p)
    assert not is_core(p)


def test_protected_alias_equals_immutable():
    """向后兼容：is_protected = is_immutable；PROTECTED_FILES = IMMUTABLE_FILES。"""
    assert is_protected(_SRC / "guard.py")
    assert not is_protected(_SRC / "main.py")  # main 现在是 core，不再硬锁
    assert PROTECTED_FILES == IMMUTABLE_FILES


def test_write_to_immutable_blocked():
    """改不可变核 → 被工具机械拒绝。"""
    from src.tools.file import write_file
    result = write_file(str(_SRC / "guard.py"), "# test modification")
    assert "不可变安全核" in result, f"应拒绝改 guard.py，实际: {result[:100]}"


def test_delete_core_blocked():
    """核心业务文件可改但不可删。"""
    from src.tools.file import delete_file
    result = delete_file(str(_SRC / "main.py"))
    assert "不可删" in result or "禁止删除" in result, f"应拒绝删 main.py，实际: {result[:100]}"


def test_resolution_error_returns_false():
    """路径解析异常返回 False（不崩）。"""
    assert not is_immutable("\0invalid")
    assert not is_core("\0invalid")


@pytest.mark.slow  # 起子进程跑 import 冒烟（~数秒），移出快速门
def test_core_edit_smoke_reverts_broken_change(monkeypatch):
    """核心安全带：核心文件被改出必崩 import → 冒烟失败 → 从备份自动回滚复原。

    用专用一次性模块 src/_smoke_probe.py（注入 CORE_FILES），直接测 _post_write_check。
    绝不原地改共享的 commands.py——后者被改坏会与 agent 并发跑测试相撞、中断即留坏源
    （这正是本测试旧版埋的雷：见 conftest 任务隔离 / 记忆 error-correction-hierarchy）。
    """
    import src.guard as guard
    from src.tools.file import _post_write_check
    probe = _SRC / "_smoke_probe.py"
    backup = _SRC / "_smoke_probe.bak"
    monkeypatch.setattr(guard, "CORE_FILES", frozenset({str(probe.resolve())}))
    try:
        backup.write_text("X = 1\n", encoding="utf-8")        # 改前快照（合法可 import）
        probe.write_text(                                     # 改后：AST 合法但 import 必崩
            "import this_module_truly_does_not_exist_xyz123\n", encoding="utf-8")
        result = _post_write_check(probe, backup)
        assert result is not None and ("安全带" in result or "冒烟" in result), (
            f"应被冒烟安全带拦下，实际: {result}")
        assert probe.read_text(encoding="utf-8") == "X = 1\n", "核心文件应已从备份回滚"
    finally:
        probe.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        (_SRC / "__pycache__" / "_smoke_probe.cpython-312.pyc").unlink(missing_ok=True)
