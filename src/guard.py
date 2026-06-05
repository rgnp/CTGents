"""自我保护 — is_protected() 阻止 agent 修改关键文件。"""

from pathlib import Path

_GUARD_FILE = Path(__file__).resolve()
PROTECTED_FILES: frozenset[str] = frozenset({
    str(_GUARD_FILE),
})


def is_protected(filepath: str | Path) -> bool:
    """检查文件是否受保护（不允许 agent 修改）。"""
    try:
        resolved = str(Path(filepath).resolve())
    except (OSError, ValueError):
        return False
    return resolved in PROTECTED_FILES
