"""自我保护 — is_protected() 阻止 agent 修改关键基础文件。"""

from pathlib import Path

_GUARD_FILE = Path(__file__).resolve()
_SRC_DIR = _GUARD_FILE.parent

PROTECTED_FILES: frozenset[str] = frozenset({
    str(_GUARD_FILE),                              # guard.py — 自我保护
    str(_SRC_DIR / "coverage_gate.py"),            # 覆盖率门禁核心
    str(_SRC_DIR / "main.py"),                     # 主循环入口
    str(_SRC_DIR / "tools" / "__init__.py"),       # 工具注册表
    str(_SRC_DIR / "validate.py"),                 # 验证流水线
})


def is_protected(filepath: str | Path) -> bool:
    """检查文件是否受保护（不允许 agent 修改）。"""
    try:
        resolved = str(Path(filepath).resolve())
    except (OSError, ValueError):
        return False
    return resolved in PROTECTED_FILES
