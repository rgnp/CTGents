"""工具调用拦截层：工具执行前机械校验不变量（强规则，不靠注意力）。

把 AGENTS.md 里『违反即:审查』的弱规则，在确定性的工具边界拦死——
长对话里注意力稀释也漏不掉：

- C10 读后写：edit_file_lines 前必须本进程读过（或写过）该文件，
  否则按行号编辑就是猜，行号错位是头号失败原因。
- C14 文件放对目录：write_file 不得在项目根新建 .py/.json/.txt/.log。

接口：check(name, args) → None 放行 / str 拒绝（execute_tool 直接回该串，不执行）。
读/写工具顺带登记到 _known_files，作为 C10 的依据。判断类规则（DRY/魔法数字）
不在此处——机械化会误伤，留给 prose + 审查。
"""

from __future__ import annotations

from pathlib import Path

_READ_TOOLS = {"read_file", "read_file_lines"}
_BANNED_ROOT_EXTS = {".py", ".json", ".txt", ".log"}
# 本进程已『见过』（读过或写过）的文件绝对路径，C10 依据
_known_files: set[str] = set()


def _project_root() -> Path:
    return Path.cwd().resolve()


def _resolve(path: str) -> Path:
    p = Path(path)
    return p.resolve() if p.is_absolute() else (_project_root() / p).resolve()


def reset_known() -> None:
    """清空『已见过文件』集（新会话/clear 时可调，使 C10 按当前对话重新计）。"""
    _known_files.clear()


def mark_known(path: str) -> None:
    """显式登记一个文件为『见过』（如外部写入后）。"""
    _known_files.add(str(_resolve(path)))


def check(name: str, args: dict) -> str | None:
    """工具执行前校验。返回 None 放行，返回 str = 拒绝消息。"""
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return None

    if name in _READ_TOOLS:
        _known_files.add(str(_resolve(path)))
        return None

    if name == "write_file":
        rejection = _check_placement(path)  # C14
        if rejection:
            return rejection
        _known_files.add(str(_resolve(path)))  # 写过即算见过
        return None

    if name == "edit_file_lines":
        return _check_read_before_edit(path)  # C10

    return None


def _check_placement(path: str) -> str | None:
    """C14：不得在项目根新建 .py/.json/.txt/.log（改已有文件不限）。"""
    target = _resolve(path)
    if target.exists():
        return None
    if target.parent == _project_root() and target.suffix in _BANNED_ROOT_EXTS:
        return (
            f"⛔ C14 拒绝：不在项目根新建 {target.name}。"
            ".py→src/，测试→tests/，文档→docs/，再试。"
        )
    return None


def _check_read_before_edit(path: str) -> str | None:
    """C10：edit_file_lines 前必须先读过该文件。"""
    target = _resolve(path)
    if not target.exists():
        return None  # 不存在 → 交给 edit 自己报错
    if str(target) not in _known_files:
        return (
            f"⛔ C10 拒绝：edit_file_lines 前必须先 read_file({path})。"
            "未读就按行号编辑是行号错位的头号原因。"
        )
    return None
