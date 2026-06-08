"""安装 git 钩子：把 scripts/git-hooks/ 下的钩子拷进 .git/hooks/。

.git/hooks 不随仓库版本管理，clone/reset 后会丢；本脚本让钩子可一键重装。
进化引擎或人工在拿到仓库后跑一次 `py scripts/install_hooks.py` 即可。
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOOKS_SRC = PROJECT_ROOT / "scripts" / "git-hooks"


def _hooks_dir() -> Path:
    """解析 .git/hooks 实际位置（兼容 worktree 与 core.hooksPath）。"""
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse 失败：{result.stderr.strip()}")
    return (PROJECT_ROOT / result.stdout.strip()).resolve()


def install() -> list[str]:
    """拷贝全部钩子并赋可执行位，返回已安装的钩子名。"""
    dest_dir = _hooks_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for src in sorted(HOOKS_SRC.iterdir()):
        if src.is_dir() or src.name.startswith("."):
            continue
        dest = dest_dir / src.name
        shutil.copyfile(src, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(src.name)
    return installed


if __name__ == "__main__":
    names = install()
    print(f"已安装钩子到 {_hooks_dir()}: {', '.join(names) or '（无）'}")
