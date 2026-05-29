"""指令系统。结构化注册：提供 name/description/usage/handler 即可。"""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from .config import SESSION_DIR
from .session import list_sessions, get_session_name, rename_session
from .tools import execute_tool


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False
    load: str = ""
    retry: bool = False


@dataclass
class Command:
    """指令描述。提供这几个字段，系统自动处理帮助和分发。"""
    name: str
    description: str = ""
    usage: str = ""
    handler: Callable[["CmdResult", list[dict], list[str], str | None], None] | None = None


# 内部注册表
_registry: list[Command] = []
_handlers: dict[str, Callable] = {}


def _add_cmd(cmd: Command) -> None:
    _registry.append(cmd)
    if cmd.handler:
        _handlers[cmd.name] = cmd.handler
        # / 前缀别名：/xxx 也能通过 xxx 调用
        if cmd.name.startswith("/") and len(cmd.name) > 1:
            _handlers.setdefault(cmd.name[1:], cmd.handler)


# ── 给内置命令用的装饰器 ──

def builtin(name: str, description: str = "", usage: str = ""):
    def deco(fn):
        _add_cmd(Command(name=name, description=description, usage=usage, handler=fn))
        return fn
    return deco


def builtin_multi(names: list[str], description: str = "", usage: str = ""):
    def deco(fn):
        for name in names:
            _add_cmd(Command(name=name, description=description, usage=usage, handler=fn))
        return fn
    return deco


# ═══════════════════════════════════════════════════════════════
# 内置指令
# ═══════════════════════════════════════════════════════════════

@builtin_multi(["/exit", "/quit", "/q"], description="退出程序")
def _cmd_exit(r: CmdResult, _msgs, _args, _sid) -> None:
    r.exit = True


@builtin_multi(["/help", "/h", "/?"], description="显示指令列表")
def _cmd_help(r: CmdResult, _msgs, _args, _sid) -> None:
    lines = ["指令列表：\n"]
    for cmd in sorted(_registry, key=lambda c: c.name):
        lines.append(f"  {cmd.name:<12} {cmd.description}")
        if cmd.usage:
            lines.append(f"  {'':<12} 用法: {cmd.usage}")
    r.message = "\n".join(lines)


@builtin_multi(["/clear", "/c"], description="清除对话上下文")
def _cmd_clear(r: CmdResult, msgs, _args, _sid) -> None:
    msgs.clear()
    r.save = True
    r.message = "上下文已清除"


@builtin("/save", description="强制保存当前会话")
def _cmd_save(r: CmdResult, _msgs, _args, _sid) -> None:
    r.save = True


@builtin("/rename", description="重命名当前会话", usage="/rename <名称>")
def _cmd_rename(r: CmdResult, _msgs, args, sid) -> None:
    if not args:
        r.message = "用法: /rename <名称>"
        return
    name = " ".join(args)
    if sid:
        rename_session(sid, name)
        r.message = f"会话已重命名为: {name}"
    else:
        r.save = True


@builtin_multi(["/sessions", "/ls"], description="列出历史会话")
def _cmd_sessions(r: CmdResult, _msgs, _args, _sid) -> None:
    sessions = list_sessions()
    if not sessions:
        r.message = "没有历史会话"
        return
    lines = ["历史会话："]
    for i, sid in enumerate(sessions, 1):
        name = get_session_name(sid)
        try:
            sp = os.path.join(SESSION_DIR, sid, "summary.txt")
            with open(sp, encoding="utf-8") as f:
                preview = f.read()[:50].replace("\n", " ")
        except Exception:
            preview = ""
        marker = "← 当前" if sid == _sid else ""
        display = name if name != sid else sid
        lines.append(f"  [{i}] {display}  {marker}")
        if preview:
            lines.append(f"         {preview}")
    r.message = "\n".join(lines)


@builtin("/load", description="切换会话", usage="/load <编号>")
def _cmd_load(r: CmdResult, _msgs, args, _sid) -> None:
    if not args:
        r.message = "用法: /load <编号>"
        return
    sessions = list_sessions()
    try:
        idx = int(args[0]) - 1
        if 0 <= idx < len(sessions):
            r.load = sessions[idx]
            r.save = True
            r.message = f"切换到: {get_session_name(r.load)}"
        else:
            r.message = f"无效编号，共 {len(sessions)} 个会话"
    except ValueError:
        r.message = f"无效编号: {args[0]}"


@builtin("/new", description="新建会话（自动保存当前）")
def _cmd_new(r: CmdResult, _msgs, _args, _sid) -> None:
    r.save = True
    r.clear = True


@builtin("/pop", description="撤回最后一条对话", usage="/pop [数量]")
def _cmd_pop(r: CmdResult, msgs, args, _sid) -> None:
    n = int(args[0]) if args else 1
    removed = 0
    for _ in range(n):
        while msgs and msgs[-1]["role"] != "user":
            msgs.pop()
        if msgs and msgs[-1]["role"] == "user":
            msgs.pop()
            removed += 1
    r.save = True
    r.message = f"已撤回 {removed} 条对话"


@builtin("/export", description="导出对话为 Markdown", usage="/export [轮数] [文件名]")
def _cmd_export(r: CmdResult, msgs, args, sid) -> None:
    count: int | None = None
    name_parts: list[str] = []
    if args:
        try:
            count = int(args[0])
            name_parts = args[1:]
        except ValueError:
            name_parts = args

    name = " ".join(name_parts) if name_parts else (get_session_name(sid) if sid else "export")
    filename = f"{name}.md" if not name.endswith(".md") else name

    messages = list(msgs)
    if count is not None:
        rounds, n = [], 0
        for m in reversed(messages):
            rounds.insert(0, m)
            if m.get("role") == "user":
                n += 1
                if n >= count:
                    break
        messages = rounds

    lines = [f"# {name}\n"]
    for m in messages:
        role, content, tc = m.get("role", ""), m.get("content", "") or "", m.get("tool_calls")
        if role == "system":
            continue
        elif role == "user":
            lines.append(f"## You\n\n{content}\n")
        elif role == "assistant":
            if tc:
                tools = ", ".join(t["function"]["name"] for t in tc)
                lines.append(f"## Agent\n\n*[调用工具: {tools}]*\n")
            if content:
                lines.append(f"{content}\n")
        elif role == "tool":
            preview = content[:120].replace("\n", " ") + ("..." if len(content) > 120 else "")
            lines.append(f"*[工具结果: {preview}]*\n")

    filepath = Path(filename)
    filepath.write_text("\n".join(lines), encoding="utf-8")
    r.message = f"已导出到: {filepath.resolve()}（{len(lines)} 行）"


@builtin("/edit", description="修改最后一条对话并重发", usage="/edit <新内容>")
def _cmd_edit(r: CmdResult, msgs, args, _sid) -> None:
    if not args:
        r.message = "用法: /edit <新内容>"
        return
    new_text = " ".join(args)
    while msgs and msgs[-1]["role"] != "user":
        msgs.pop()
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    msgs.append({"role": "user", "content": new_text})
    r.save = True
    r.retry = True
    r.message = f"已修改: {new_text}"


@builtin("/run", description="直接调用工具", usage="/run <工具名> <参数=值...>")
def _cmd_run(r: CmdResult, _msgs, args, _sid) -> None:
    if not args:
        r.message = "用法: /run <工具名> <参数=值...>"
        return
    name = args[0]
    raw = " ".join(args[1:])
    tool_args: dict[str, str] = {}
    if raw:
        for m in re.finditer(r'(\w+)=(.+?)(?=\s+\w+=|$)', raw):
            tool_args[m.group(1)] = m.group(2).strip()
    tc = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(tool_args)))
    r.message = execute_tool(tc)


@builtin("/plugin", description="列出/调用插件", usage="/plugin [工具名] [参数=值...]")
def _cmd_plugin(r: CmdResult, _msgs, args, _sid) -> None:
    from .tools.plugin_mgr import _plugins
    if not args:
        if not _plugins:
            r.message = "未安装任何插件。"
            return
        lines = [f"已安装插件 ({len(_plugins)} 个)：\n"]
        for pname, mod in _plugins.items():
            desc = getattr(mod, "DESCRIPTION", "（无描述）")
            tools = [t["function"]["name"] for t in getattr(mod, "TOOLS", [])]
            lines.append(f"  {pname}")
            lines.append(f"    {desc}")
            if tools:
                lines.append(f"    工具: {', '.join(tools)}")
            lines.append("")
        r.message = "\n".join(lines).strip()
        return

    name = args[0]
    for mod in _plugins.values():
        if hasattr(mod, "TOOLS") and any(t["function"]["name"] == name for t in mod.TOOLS):
            raw = " ".join(args[1:])
            tool_args: dict[str, str] = {}
            if raw:
                for m in re.finditer(r'(\w+)=(.+?)(?=\s+\w+=|$)', raw):
                    tool_args[m.group(1)] = m.group(2).strip()
            tc = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(tool_args)))
            r.message = execute_tool(tc)
            return
    r.message = f"未找到插件工具: {name}"


# ═══════════════════════════════════════════════════════════════
# 分发
# ═══════════════════════════════════════════════════════════════

def dispatch(user_input: str, messages: list[dict], session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = _handlers.get(cmd)
    if handler:
        handler(r, messages, args, session_id)
    return r


def register_plugin_commands() -> None:
    """扫描插件，将其 COMMANDS 注册到指令系统。"""
    from .tools.plugin_mgr import _plugins
    for mod in _plugins.values():
        cmds = getattr(mod, "COMMANDS", None)
        if cmds is None:
            continue
        # 支持两种格式：list[Command] 或 dict{name: handler}
        if isinstance(cmds, dict):
            for cmd_name, handler in cmds.items():
                c = Command(name=cmd_name, handler=handler)
                _add_cmd(c)
        elif isinstance(cmds, list):
            for c in cmds:
                if isinstance(c, Command):
                    _add_cmd(c)
                elif isinstance(c, dict) and "name" in c:
                    _add_cmd(Command(**c))

# 启动时加载插件指令
register_plugin_commands()
