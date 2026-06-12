"""自我保护 — is_protected() 阻止 agent 修改关键基础文件。

PROTECTED_FILES 列表里的文件，任何 write_file/edit_file_lines/delete_file 操作
都会被 file.py 的 is_protected() 机械拦截——不靠 LLM 自觉，不可绕过。
"""

from pathlib import Path

_GUARD_FILE = Path(__file__).resolve()
_SRC_DIR = _GUARD_FILE.parent
_PROJECT_ROOT = _SRC_DIR.parent

PROTECTED_FILES: frozenset[str] = frozenset({
    str(_GUARD_FILE),                                                  # guard.py
    str(_SRC_DIR / "tool_guard.py"),                                   # 工具拦截层
    str(_SRC_DIR / "main.py"),                                         # 主循环入口
    str(_SRC_DIR / "tools" / "__init__.py"),                           # 工具注册表
    str(_SRC_DIR / "validate.py"),                                     # 验证流水线
    str(_SRC_DIR / "commands.py"),                                     # 指令派发
    str(_PROJECT_ROOT / "AGENTS.md"),                                  # AI 操作手册
    str(_PROJECT_ROOT / "scripts" / "git-hooks" / "pre-commit"),       # 提交硬闸
})


def is_protected(filepath: str | Path) -> bool:
    """检查文件是否受保护（不允许 agent 修改）。"""
    try:
        resolved = str(Path(filepath).resolve())
    except (OSError, ValueError):
        return False
    return resolved in PROTECTED_FILES
