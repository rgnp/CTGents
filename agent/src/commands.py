"""指令系统。添加新指令：@register("/xxx") + 函数。"""

import os
from collections.abc import Callable
from dataclasses import dataclass

from .config import SESSION_DIR
from .session import list_sessions


@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False


CmdHandler = Callable[["CmdResult", list[dict], list[str]], None]

COMMANDS: dict[str, CmdHandler] = {}


def register(*names: str):
    def deco(fn: CmdHandler):
        for name in names:
            COMMANDS[name] = fn
        return fn
    return deco


@register("/exit", "/quit", "/q")
def _cmd_exit(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """退出程序"""
    r.exit = True


@register("/help", "/h", "/?")
def _cmd_help(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """显示指令列表"""
    lines = ["可用指令："]
    for name in sorted(COMMANDS):
        doc = COMMANDS[name].__doc__ or ""
        lines.append(f"  {name:<12} {doc}")
    r.message = "\n".join(lines)


@register("/clear", "/c")
def _cmd_clear(r: CmdResult, msgs: list[dict], _args: list[str]) -> None:
    """清除对话上下文"""
    msgs.clear()
    r.message = "上下文已清除"


@register("/save")
def _cmd_save(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """强制保存当前会话"""
    r.save = True


@register("/sessions", "/ls")
def _cmd_sessions(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """列出历史会话"""
    sessions = list_sessions()
    if not sessions:
        r.message = "没有历史会话"
        return
    lines = ["历史会话："]
    for i, sid in enumerate(sessions, 1):
        summary_path = os.path.join(SESSION_DIR, sid, "summary.txt")
        preview = ""
        try:
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
                    preview = f.read()[:60].replace("\n", " ")
        except Exception:
            pass
        lines.append(f"  [{i}] {sid}  {preview}")
    r.message = "\n".join(lines)


@register("/new")
def _cmd_new(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """新建会话（自动保存当前）"""
    r.save = True
    r.clear = True


def dispatch(user_input: str, messages: list[dict]) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler:
        handler(r, messages, args)
    return r
