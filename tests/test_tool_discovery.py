"""测试工具自动发现 + 语法自检 + 注册表故障隔离。

进化产物只需把工具文件放进 src/tools/ 即自动注册（无需手改核心清单），
且坏文件/坏模块被隔离跳过而非崩溃启动——消除"手改注册表导致启动崩溃"的整类故障。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.tools as tools_pkg
from src.tools import _tool_meta, get_failed_modules, get_tools


def test_discovers_real_tool_modules():
    """自动发现含 TOOLS_* + execute 的真实工具文件，含进化长出的 paper。"""
    mods = _tool_meta._discover_builtin_modules()
    stems = {p for p, _, _ in mods}
    for expected in (".web", ".file", ".self", ".paper"):
        assert expected in stems, f"{expected} 未被发现"
    # 每条都是 (路径, TOOLS_*, execute)
    for _path, tools_attr, exec_attr in mods:
        assert tools_attr.startswith("TOOLS_")
        assert exec_attr == "execute"


def test_skips_underscore_and_helper_files():
    """私有文件（_ 前缀）与无 TOOLS_* 的辅助文件不入清单。"""
    stems = {p for p, _, _ in _tool_meta._discover_builtin_modules()}
    assert ".__init__" not in stems
    assert "._tool_meta" not in stems
    # storm/tokens 是辅助模块，无 TOOLS_*
    assert ".storm" not in stems


def test_syntax_error_file_skipped_not_crash(tmp_path, monkeypatch):
    """工具目录里有语法错误文件 → 被跳过并记录，不让发现崩溃。"""
    (tmp_path / "goodtool.py").write_text(
        "TOOLS_X = []\ndef execute(name, args):\n    return None\n", encoding="utf-8"
    )
    (tmp_path / "badtool.py").write_text(
        "TOOLS_Y = [\ndef execute(  # 语法错误\n", encoding="utf-8"
    )
    (tmp_path / "helper.py").write_text("x = 1\n", encoding="utf-8")  # 无 TOOLS_*
    (tmp_path / "_priv.py").write_text("TOOLS_Z = []\ndef execute(): ...\n", encoding="utf-8")
    monkeypatch.setattr(_tool_meta, "_TOOLS_DIR", tmp_path)

    mods = _tool_meta._discover_builtin_modules()
    stems = {p for p, _, _ in mods}
    assert ".goodtool" in stems          # 好文件被发现
    assert ".badtool" not in stems       # 坏文件被跳过
    assert ".helper" not in stems        # 无 TOOLS_* 不收
    assert "._priv" not in stems         # 私有跳过

    skipped = {name for name, _ in _tool_meta.get_discovery_skipped()}
    assert "badtool.py" in skipped       # 语法错误被记录


def test_registry_isolates_broken_module(monkeypatch):
    """导入会失败的模块被隔离，其余工具照常注册，系统保持可用。"""
    real = list(_tool_meta._BUILTIN_MODULES)
    # refresh 会重算真实目录，测试时禁用它以注入坏条目
    monkeypatch.setattr(_tool_meta, "refresh_modules", lambda: None)
    monkeypatch.setattr(
        _tool_meta, "_BUILTIN_MODULES",
        real + [(".__nonexistent_broken_xyz", "TOOLS_X", "execute")],
    )
    try:
        tools_pkg._init_registry()
        failed = {full for full, _ in get_failed_modules()}
        assert any("__nonexistent_broken_xyz" in f for f in failed), \
            f"坏模块应被隔离记录，实际: {failed}"
        assert len(get_tools()) > 10  # 其余工具仍在
    finally:
        monkeypatch.undo()
        tools_pkg._init_registry()  # 还原真实注册表


def test_healthy_registry_no_failures():
    """正常初始化后无故障模块。"""
    tools_pkg._init_registry()
    assert get_failed_modules() == []
