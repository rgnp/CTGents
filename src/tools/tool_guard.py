"""工具调用拦截层：工具执行前机械校验不变量（强规则，不靠注意力）。

把 AGENTS.md 里『违反即:审查』的弱规则，在确定性的工具边界拦死——
长对话里注意力稀释也漏不掉：

- C10 读后写：edit_file_lines 前必须本进程读过（或写过）该文件，
  否则按行号编辑就是猜，行号错位是头号失败原因。任何改变总行数的编辑
  （insert/delete/行数变了的多行 replace）后作废"已读"，逼下次 edit 重读拿新
  行号——挡掉"叠着按旧行号连改 → 改残文件"。键于不变量（行数变没变）而非动作。
- C14 文件放对目录：write_file 不得在项目根新建 .py/.json/.txt/.log。
- P1/P2 危险命令：run_command 拦 `git add -A`/`git add .`、force-push 到 main/master
  （确定的命令模式，零判断——P1 还正好对抗模型 `git add -A` 的强默认）。

接口：check(name, args) → None 放行 / str 拒绝（execute_tool 直接回该串，不执行）。
读/写工具顺带登记到 _known_files，作为 C10 的依据。判断类规则（DRY/魔法数字）
不在此处——机械化会误伤，留给 prose + 审查。
"""

from __future__ import annotations

import re
from pathlib import Path

_READ_TOOLS = {"read_file", "read_file_lines"}
_BANNED_ROOT_EXTS = {".py", ".json", ".txt", ".log"}
# 本进程已『见过』（读过或写过）的文件绝对路径，C10 依据
_known_files: set[str] = set()

# P1：git add -A / --all / 裸点（git add . 末尾，不含 ./path 这种具体路径）
_GIT_ADD_ALL = re.compile(r"\bgit\s+add\s+(?:-A\b|--all\b|\.(?:\s|$))")
# P2：git push 同时带 force 旗标与 main/master（两个 lookahead 不依赖参数顺序）
_GIT_FORCE_PUSH_MAIN = re.compile(
    r"\bgit\s+push\b(?=.*(?:--force|--force-with-lease|\s-f\b))(?=.*\b(?:main|master)\b)"
)


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
    if name == "run_command":
        return _check_command(args.get("command"))  # P1/P2

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
        rejection = _check_read_before_edit(path)  # C10
        if rejection:
            return rejection
        # 任何改变总行数的编辑 → 其后行号全部失效。作废"已读"，逼下次 edit 先重读拿新
        # 行号。键于不变量（行数变没变）而非动作类型：insert/delete/行数变了的多行
        # replace 一网打尽，不为每种动作各打补丁（曾 insert/delete 漂移误删 P1；又多行
        # replace 漂移把验证节改出重复标题+删 bullet）。
        if _edit_changes_line_count(args):
            _known_files.discard(str(_resolve(path)))
        return None

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


def _edit_changes_line_count(args: dict) -> bool:
    """该次 edit_file_lines 会不会改变文件总行数（→ 其后行号失效）。

    insert 加行、delete 删行 → 必变；replace 仅当新行数 ≠ 被替区间行数才变
    （单行换单行不漂移，多行换 N 行漂移）。信息不全（真实 replace 必带 end_line+
    new_lines，否则工具自身报错）→ 当作不变，不误作废、不破坏单行 replace 连改。
    """
    action = args.get("action")
    if action in ("insert", "delete"):
        return True
    if action == "replace":
        start, end, new_lines = args.get("start_line"), args.get("end_line"), args.get("new_lines")
        if isinstance(start, int) and isinstance(end, int) and isinstance(new_lines, str):
            return len(new_lines.split("\n")) != (end - start + 1)
    return False


def _check_command(command: str | None) -> str | None:
    """P1/P2：在 run_command 边界拦危险 git 命令（确定模式，零判断）。"""
    if not isinstance(command, str):
        return None
    if _GIT_ADD_ALL.search(command):
        return (
            "⛔ P1 拒绝：禁止 git add -A / git add . —— 只暂存具体文件"
            "（git add <path> ...），避免卷入无关改动。"
        )
    if _GIT_FORCE_PUSH_MAIN.search(command):
        return "⛔ P2 拒绝：禁止 force-push 到 main/master（会重写主干历史）。"
    return None
