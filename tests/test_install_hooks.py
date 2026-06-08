"""测试 git 钩子安装器：core.hooksPath 指向版本管理的 scripts/git-hooks。"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import install_hooks


def test_install_sets_hookspath(monkeypatch):
    """install() 调 git config core.hooksPath 指向 scripts/git-hooks。"""
    calls = []

    def fake_git(args):
        calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install_hooks, "_git", fake_git)
    monkeypatch.setattr(install_hooks, "_mark_hooks_executable", lambda: None)

    result = install_hooks.install()

    assert result == install_hooks.HOOKS_PATH_VALUE
    assert ["config", "core.hooksPath", install_hooks.HOOKS_PATH_VALUE] in calls


def test_hookspath_value_is_posix_relative():
    """配置值是 posix 相对路径，跨平台一致。"""
    assert install_hooks.HOOKS_PATH_VALUE == "scripts/git-hooks"


def test_ensure_installed_idempotent_when_already_set(monkeypatch):
    """已指向本仓库钩子 → ensure 不动、返回 False。"""
    monkeypatch.setattr(install_hooks, "_current_hooks_path", lambda: install_hooks.HOOKS_PATH_VALUE)
    called = {"install": False}
    monkeypatch.setattr(install_hooks, "install", lambda: called.update(install=True))
    assert install_hooks.ensure_installed() is False
    assert called["install"] is False


def test_ensure_installed_sets_when_missing(monkeypatch):
    """未设置 → ensure 调 install、返回 True。"""
    monkeypatch.setattr(install_hooks, "_current_hooks_path", lambda: "")
    called = {"install": False}
    monkeypatch.setattr(install_hooks, "install", lambda: called.update(install=True))
    assert install_hooks.ensure_installed() is True
    assert called["install"] is True


def test_pre_commit_source_runs_full_suite():
    """钩子源码确实跑全量 pytest（无 -k/路径过滤），守住"全绿"语义。"""
    src = (install_hooks.HOOKS_SRC / "pre-commit").read_text(encoding="utf-8")
    assert "pytest" in src
    assert "ruff check src/" in src
    assert " -k " not in src  # 不得退化成只跑子集


def test_pre_commit_tests_staged_snapshot():
    """钩子对暂存快照跑（先 stash 未暂存），而非工作树。"""
    src = (install_hooks.HOOKS_SRC / "pre-commit").read_text(encoding="utf-8")
    assert "stash push --keep-index" in src
    assert "stash pop" in src
