"""指令系统。添加新指令：@register("/xxx") + 函数。"""

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
from .tools.plugin_mgr import discover_plugins


@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False
    load: str = ""
    retry: bool = False


CmdHandler = Callable[["CmdResult", list[dict], list[str], str | None], None]

COMMANDS: dict[str, CmdHandler] = {}


def register(*names: str):
    def deco(fn: CmdHandler):
        for name in names:
            COMMANDS[name] = fn
        return fn
    return deco



# ═══════════════════════════════════════════════════════════════
# 内置指令
# ═══════════════════════════════════════════════════════════════

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
    r.save = True
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
            r.save = True
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


@register("/pop")
def _cmd_pop(r: CmdResult, msgs: list[dict], args: list[str], _sid: str | None) -> None:
    """撤回最后一条对话  /pop [数量]"""
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


@register("/export")
def _cmd_export(r: CmdResult, msgs: list[dict], args: list[str], sid: str | None) -> None:
    """导出对话为 Markdown  /export [轮数] [文件名]"""
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
        rounds = []
        user_count = 0
        for m in reversed(messages):
            rounds.insert(0, m)
            if m.get("role") == "user":
                user_count += 1
                if user_count >= count:
                    break
        messages = rounds

    lines = [f"# {name}\n"]
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "") or ""
        tc = m.get("tool_calls")
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

    text = "\n".join(lines)
    filepath = Path(filename)
    filepath.write_text(text, encoding="utf-8")
    r.message = f"已导出到: {filepath.resolve()}（{len(text)} 字符）"


@register("/edit")
def _cmd_edit(r: CmdResult, msgs: list[dict], args: list[str], _sid: str | None) -> None:
    """修改并重发最后一条对话  /edit <新内容>"""
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


@register("/run")
def _cmd_run(r: CmdResult, _msgs: list[dict], args: list[str], _sid: str | None) -> None:
    """直接调用工具  /run <工具名> <参数=值...>"""
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


# ═══════════════════════════════════════════════════════════════
# 插件指令（agent 可通过 install_plugin 扩展）
# ═══════════════════════════════════════════════════════════════

@register("/plugin")
def _cmd_plugin(r: CmdResult, _msgs: list[dict], args: list[str], _sid: str | None) -> None:
    """插件快捷入口  /plugin <插件名> [参数=值...]  或 /plugin 列出所有"""
    if not args:
        # 列出所有插件及其工具
        from .tools.plugin_mgr import _plugins
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
    # 找匹配的插件工具
    from .tools.plugin_mgr import _plugins
    for mod in _plugins.values():
        if hasattr(mod, "TOOLS") and any(
            t["function"]["name"] == name for t in mod.TOOLS
        ):
            raw = " ".join(args[1:])
            tool_args: dict[str, str] = {}
            if raw:
                for m in re.finditer(r'(\w+)=(.+?)(?=\s+\w+=|$)', raw):
                    tool_args[m.group(1)] = m.group(2).strip()
            tc = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(tool_args)))
            r.message = execute_tool(tc)
            return

    r.message = f"未找到插件工具: {name}\n（用 /plugin 查看所有可用插件工具）"


# ═══════════════════════════════════════════════════════════════
# 分发
# ═══════════════════════════════════════════════════════════════

def dispatch(user_input: str, messages: list[dict], session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler:
        handler(r, messages, args, session_id)
    return r


def register_plugin_commands() -> None:
    """扫描已加载插件，将其 COMMANDS 注册到指令系统。可随时调用来刷新。"""
    from .tools.plugin_mgr import _plugins
    for mod in _plugins.values():
        if hasattr(mod, "COMMANDS") and isinstance(mod.COMMANDS, dict):
            for cmd_name, handler in mod.COMMANDS.items():
                COMMANDS[cmd_name] = handler


# 启动时加载
register_plugin_commands()
