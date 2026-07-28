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
读工具登记到 _read_files 作为 C10 依据；写工具记入 _written_files 但不给 C10 开绿灯。
不在此处——机械化会误伤，留给 prose + 审查。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import resolve_runtime_path

_READ_TOOLS = {"read_file", "read_file_lines"}
_BANNED_ROOT_EXTS = {".py", ".json", ".txt", ".log"}
# 本进程已『读过』的文件绝对路径，C10 依据（write_file 不加入此集——写过≠知道行号）
_read_files: set[str] = set()
# 本进程已『写过』的文件（供未来审计/其他规则用，但不给 C10 开绿灯）
_written_files: set[str] = set()

# P1：git add -A / --all / 裸点（git add . 末尾，不含 ./path 这种具体路径）
_GIT_ADD_ALL = re.compile(r"\bgit\s+add\s+(?:-A\b|--all\b|\.(?:\s|$))")
# P2：git push 同时带 force 旗标与 main/master（两个 lookahead 不依赖参数顺序）
_GIT_FORCE_PUSH_MAIN = re.compile(
    r"\bgit\s+push\b(?=.*(?:--force|--force-with-lease|\s-f\b))(?=.*\b(?:main|master)\b)"
)


def _project_root() -> Path:
    return Path.cwd().resolve()


def _resolve(path: str) -> Path:
    return resolve_runtime_path(path, _project_root())


def reset_known() -> None:
    """清空『已读过/已写过』文件集（新会话/clear 时可调，使 C10 按当前对话重新计）。"""
    _read_files.clear()
    _written_files.clear()


def mark_known(path: str) -> None:
    """显式登记一个文件为『已读过』（如外部写入后）。"""
    _read_files.add(str(_resolve(path)))


def check(name: str, args: dict) -> str | None:
    """工具执行前校验。返回 None 放行，返回 str = 拒绝消息。"""
    if name == "run_command":
        return _check_command(args.get("command"))  # P1/P2

    path = args.get("path")
    if not isinstance(path, str) or not path:
        return None

    if name in _READ_TOOLS:
        _read_files.add(str(_resolve(path)))
        return None

    if name == "write_file":
        rejection = _check_placement(path)  # C14
        if rejection:
            return rejection
        _written_files.add(str(_resolve(path)))  # 记入"已写过"
        # 任何写入都作废已读——文件内容已变，行号对应的东西不一样了
        _read_files.discard(str(_resolve(path)))
        return None

    if name == "edit_file_lines":
        rejection = _check_read_before_edit(path)  # C10
        if rejection:
            return rejection
        # 任何编辑都使行号失效，无论行数变没变——
        # 等长 replace 行数不变但内容已变，下一轮 edit 拿旧行号指的就是别的东西了。
        _read_files.discard(str(_resolve(path)))
        return None

    return None


def _check_placement(path: str) -> str | None:
    """C14：不得在项目根新建受限后缀；不得在 src/tools/ 下新建 .py。

    改已有文件不限（编辑已有工具文件走 C10 读后写 + guard 保护关键文件）。
    """
    target = _resolve(path)
    if target.exists():
        return None
    root = _project_root()
    # 项目根：禁止 .py/.json/.txt/.log
    if target.parent == root and target.suffix in _BANNED_ROOT_EXTS:
        return (
            f"⛔ C14 拒绝：不在项目根新建 {target.name}。"
            ".py→src/，测试→tests/，文档→docs/，再试。"
        )
    # src/tools/ 下禁止新建 .py（热加载使新工具立即生效，需人工审查）
    tools_dir = root / "src" / "tools"
    if target.suffix == ".py":
        try:
            target.relative_to(tools_dir)
            return (
                f"⛔ C14 拒绝：不在 src/tools/ 下新建工具模块 {target.name}。"
                "工具注册需经人工审查——热加载会使新工具立即生效。"
                "编辑已有工具文件不受此限。"
            )
        except ValueError:
            pass
    return None


def _check_read_before_edit(path: str) -> str | None:
    """C10：edit_file_lines 前必须先读过该文件（write_file 不赋予读权限）。"""
    target = _resolve(path)
    if not target.exists():
        return None  # 不存在 → 交给 edit 自己报错
    if str(target) not in _read_files:
        return (
            f"⛔ C10 拒绝：edit_file_lines 前必须先 read_file({path})。"
            "未读就按行号编辑是行号错位的头号原因。"
        )
    return None


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
