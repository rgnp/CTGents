"""测试 git 钩子安装器：能把 scripts/git-hooks 拷进 .git/hooks 并赋可执行位。"""

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import install_hooks


def test_install_copies_pre_commit(tmp_path, monkeypatch):
    """install() 把 pre-commit 拷到解析出的 hooks 目录，并可执行。"""
    hooks = tmp_path / "hooks"
    monkeypatch.setattr(install_hooks, "_hooks_dir", lambda: hooks)

    installed = install_hooks.install()

    assert "pre-commit" in installed
    dest = hooks / "pre-commit"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == (
        install_hooks.HOOKS_SRC / "pre-commit"
    ).read_text(encoding="utf-8")
    if os.name != "nt":  # Windows 不强制 POSIX 可执行位
        assert dest.stat().st_mode & stat.S_IEXEC


def test_install_skips_dotfiles(tmp_path, monkeypatch):
    """点开头/目录不当作钩子拷贝。"""
    hooks = tmp_path / "hooks"
    monkeypatch.setattr(install_hooks, "_hooks_dir", lambda: hooks)

    installed = install_hooks.install()

    assert all(not name.startswith(".") for name in installed)


def test_pre_commit_source_runs_full_suite():
    """钩子源码确实跑全量 pytest（无 -k/路径过滤），守住"全绿"语义。"""
    src = (install_hooks.HOOKS_SRC / "pre-commit").read_text(encoding="utf-8")
    assert "pytest" in src
    assert "ruff check src/" in src
    assert " -k " not in src  # 不得退化成只跑子集
