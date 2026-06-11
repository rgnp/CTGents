"""门通行证审计：核对 HEAD 提交是否经过质量门（pre-commit 钩子）。

钩子在门禁通过时把暂存树哈希追加到 .git/ctg-gate-passed；
本审计取 HEAD 的树哈希核对记录。键于「提交的树」而非「绕门的动作」——
所以任何绕门路径（--no-verify / 换钩子 / 脚本包装）事后都核得出来。
零监督：不需要人盯，会话启动时自动注入提醒（同 plan-mode 删除时定下的
"审计替代门禁保姆"原则）。

记录文件不存在 = 机制未部署/新克隆 → 静默；有记录但 HEAD 不在 → 提醒。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 通行证记录文件名（位于 .git/ 内，天然不入库）
_RECORD_NAME = "ctg-gate-passed"


def _git_line(args: list[str]) -> str:
    """跑 git 子命令取单行输出；任何失败返回空串（审计不阻塞启动）。"""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=10, cwd=str(PROJECT_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def head_gate_notice() -> str:
    """HEAD 无门通行证时返回提醒文本；正常或无法判定返回空串。"""
    git_dir = _git_line(["rev-parse", "--git-dir"])
    if not git_dir:
        return ""
    gd = Path(git_dir)
    if not gd.is_absolute():
        gd = PROJECT_ROOT / gd
    record = gd / _RECORD_NAME
    if not record.exists():
        return ""

    head_tree = _git_line(["rev-parse", "HEAD^{tree}"])
    if not head_tree:
        return ""
    try:
        passed = set(record.read_text(encoding="utf-8").split())
    except OSError:
        return ""
    if head_tree in passed:
        return ""

    head_short = _git_line(["rev-parse", "--short", "HEAD"]) or "HEAD"
    return (
        f"⚠ 门通行证审计：HEAD 提交（{head_short}）的树哈希不在质量门通过记录中，"
        f"该提交可能绕过了 pre-commit 门（--no-verify 等）。"
        f"若是用户人工知情放行可忽略；否则先跑全量 pytest 确认主干是绿的，"
        f"红了先修再继续其他工作。"
    )
