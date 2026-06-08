"""安装 git 钩子：把 core.hooksPath 指向版本管理的 scripts/git-hooks/。

不再拷贝到 .git/hooks/（那样 clone/reset 后会丢，且改源不重装就漂移）。
改用 core.hooksPath → git 直接用被版本管理的钩子：永远在、改了即时生效、零漂移。
core.hooksPath 是 per-clone 本地配置（不随仓库提交），所以新克隆仍需设一次——
`ensure_installed()` 幂等，可在 main.py 启动时调用，彻底堵掉"克隆后没钩子"。
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOOKS_SRC = PROJECT_ROOT / "scripts" / "git-hooks"
# git config 用 posix 相对路径，跨平台一致
HOOKS_PATH_VALUE = HOOKS_SRC.relative_to(PROJECT_ROOT).as_posix()


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _current_hooks_path() -> str:
    result = _git(["config", "--local", "--get", "core.hooksPath"])
    return result.stdout.strip() if result.returncode == 0 else ""


def _mark_hooks_executable() -> None:
    for hook in HOOKS_SRC.iterdir():
        if hook.is_file() and not hook.name.startswith("."):
            hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def install() -> str:
    """设 core.hooksPath 指向 scripts/git-hooks 并赋可执行位。返回设定值。"""
    result = _git(["config", "core.hooksPath", HOOKS_PATH_VALUE])
    if result.returncode != 0:
        raise RuntimeError(f"git config 失败：{result.stderr.strip()}")
    _mark_hooks_executable()
    return HOOKS_PATH_VALUE


def ensure_installed() -> bool:
    """幂等：未指向本仓库钩子才设置。返回是否做了改动（供启动时静默调用）。"""
    if _current_hooks_path() == HOOKS_PATH_VALUE:
        return False
    install()
    return True


if __name__ == "__main__":
    print(f"core.hooksPath → {install()}")
