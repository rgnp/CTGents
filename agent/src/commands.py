"""指令系统。添加新指令：@register("/xxx") + 函数。"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import SESSION_DIR
from .session import list_sessions, get_session_name, rename_session


@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False
    load: str = ""              # 要切换到的会话 ID，空串表示不切换


CmdHandler = Callable[["CmdResult", list[dict], list[str], str | None], None]

COMMANDS: dict[str, CmdHandler] = {}


def register(*names: str):
    def deco(fn: CmdHandler):
        for name in names:
            COMMANDS[name] = fn
        return fn
    return deco


@register("/exit", "/quit", "/q")
def _cmd_exit(r: CmdResult, _msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """退出程序"""
    r.exit = True


@register("/help", "/h", "/?")
def _cmd_help(r: CmdResult, _msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """显示指令列表"""
    lines = ["可用指令："]
    for name in sorted(COMMANDS):
        doc = COMMANDS[name].__doc__ or ""
        lines.append(f"  {name:<14} {doc}")
    r.message = "\n".join(lines)


@register("/clear", "/c")
def _cmd_clear(r: CmdResult, msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """清除对话上下文"""
    msgs.clear()
    r.message = "上下文已清除"


@register("/save")
def _cmd_save(r: CmdResult, _msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """强制保存当前会话"""
    r.save = True


@register("/rename")
def _cmd_rename(r: CmdResult, msgs: list[dict], args: list[str], sid: str | None) -> None:
    """重命名当前会话  /rename <名称>"""
    if not args:
        r.message = "用法: /rename <名称>"
        return
    name = " ".join(args)
    if sid:
        rename_session(sid, name)
        r.message = f"会话已重命名为: {name}"
    else:
        r.save = True
        r.message = ""  # 静默保存，再输一次 /rename 即可


@register("/sessions", "/ls")
def _cmd_sessions(r: CmdResult, _msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """列出历史会话"""
    sessions = list_sessions()
    if not sessions:
        r.message = "没有历史会话"
        return
    lines = ["历史会话："]
    for i, sid in enumerate(sessions, 1):
        name = get_session_name(sid)
        summary_path = os.path.join(SESSION_DIR, sid, "summary.txt")
        preview = ""
        try:
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
                    preview = f.read()[:50].replace("\n", " ")
        except Exception:
            pass
        marker = "← 当前" if sid == _sid else ""
        if name != sid:
            lines.append(f"  [{i}] {name}  {marker}")
            lines.append(f"         {sid}  {preview}")
        else:
            lines.append(f"  [{i}] {sid}  {preview}  {marker}")
    r.message = "\n".join(lines)


@register("/load")
def _cmd_load(r: CmdResult, _msgs: list[dict], args: list[str], _sid: str | None) -> None:
    """切换会话  /load <编号>"""
    if not args:
        r.message = "用法: /load <编号>"
        return
    sessions = list_sessions()
    try:
        idx = int(args[0]) - 1
        if 0 <= idx < len(sessions):
            target = sessions[idx]
            name = get_session_name(target)
            r.load = target
            r.save = True        # 先保存当前
            r.message = f"切换到: {name}"
        else:
            r.message = f"无效编号，共 {len(sessions)} 个会话"
    except ValueError:
        r.message = f"无效编号: {args[0]}"


@register("/new")
def _cmd_new(r: CmdResult, _msgs: list[dict], _args: list[str], _sid: str | None) -> None:
    """新建会话（自动保存当前）"""
    r.save = True
    r.clear = True


def dispatch(user_input: str, messages: list[dict], session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler:
        handler(r, messages, args, session_id)
    return r
