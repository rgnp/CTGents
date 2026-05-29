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
    # 按 handler 去重，同 handler 的别名合并显示
    seen: dict[int, list[Command]] = {}
    for cmd in _registry:
        hid = id(cmd.handler)
        seen.setdefault(hid, []).append(cmd)

    lines = ["指令列表：\n"]
    for group in sorted(seen.values(), key=lambda g: g[0].name):
        primary = group[0]
        aliases = [c.name for c in group[1:]]
        if aliases:
            name_display = f"{primary.name}（{'、'.join(aliases)}）"
        else:
            name_display = primary.name
        lines.append(f"  {name_display:<20} {primary.description}")
        if primary.usage:
            lines.append(f"  {'':<20} 用法: {primary.usage}")
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
# 热加载指令
# ═══════════════════════════════════════════════════════════════

@builtin("/reload", description="热加载指令系统（改 commands.py 后无需重启）")
def _cmd_reload(r: CmdResult, _msgs, _args, _sid) -> None:
    """重新加载 commands 模块自身，使新增/修改的指令立即生效。"""
    from .tools.plugin_mgr import _plugins as plugin_cache
    plugin_cache.clear()
    r.message = "🔄 触发热加载，请稍后..."


# ═══════════════════════════════════════════════════════════════
# Skill 指令
# ═══════════════════════════════════════════════════════════════

def _call_skill_tool(name: str, args: dict | None = None) -> str:
    """通过 execute_tool 调用 skill 插件工具。"""
    tc = SimpleNamespace(
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(args or {}, ensure_ascii=False)
        )
    )
    return execute_tool(tc)


@builtin_multi(["/skill", "/skills"], description="Skill 管理：列出/查看/加载等", usage="/skill <子命令> [参数]")
def _cmd_skill(r: CmdResult, _msgs, args, _sid) -> None:
    if not args:
        result = _call_skill_tool("skill_auto_discover")
        r.message = result
        return

    sub = args[0]
    rest = args[1:]

    if sub in ("list", "ls"):
        result = _call_skill_tool("skill_auto_discover")
        r.message = result

    elif sub in ("show", "view", "info"):
        if not rest:
            r.message = "用法: /skill show <名称>"
            return
        result = _call_skill_tool("skill_show", {"skill_path": " ".join(rest)})
        r.message = result

    elif sub in ("load", "use", "activate"):
        if not rest:
            r.message = "用法: /skill load <名称>"
            return
        result = _call_skill_tool("skill_auto_load", {"skill_name": " ".join(rest)})
        r.message = result

    elif sub in ("context", "active", "current"):
        result = _call_skill_tool("skill_auto_context")
        r.message = result

    elif sub in ("reload", "refresh", "rescan"):
        result = _call_skill_tool("skill_auto_reload")
        r.message = result

    elif sub in ("create", "new"):
        if len(rest) < 2:
            r.message = "用法: /skill create <名称> <描述>"
            return
        name = rest[0]
        desc = " ".join(rest[1:])
        r.message = (
            f"创建 Skill 请使用工具接口更完善。\n"
            f"试试: skill_create name=\"{name}\" description=\"{desc}\" instructions=\"...\""
        )

    elif sub in ("validate", "check"):
        if not rest:
            r.message = "用法: /skill validate <路径>"
            return
        result = _call_skill_tool("skill_validate", {"filepath": " ".join(rest)})
        r.message = result

    else:
        r.message = (
            f"未知子命令: {sub}\n"
            f"可用子命令：list、show、load、context、reload、create、validate"
        )


# ═══════════════════════════════════════════════════════════════
# 状态指令
# ═══════════════════════════════════════════════════════════════

@builtin("/status", description="系统状态概览：插件、Skill、会话、配置一览")
def _cmd_status(r: CmdResult, msgs, _args, sid) -> None:
    from .config import (
        MAX_CONTEXT_TOKENS, TOOL_RESULT_BUDGET, TOKEN_PER_CHAR,
        TOOL_LOOP_THRESHOLD, MAX_RETRIES, DEEPSEEK_MODEL,
        SESSION_DIR, PLUGINS_DIR,
    )
    from .tools.plugin_mgr import _plugins
    from .tools.tokens import count_messages_tokens
    from .session import list_sessions

    # ── 插件 ──
    plugin_count = len(_plugins)
    tool_count = sum(len(getattr(mod, "TOOLS", [])) for mod in _plugins.values())

    # ── Skill ──
    skills_dir = Path("skills")
    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()] if skills_dir.is_dir() else []
    skill_count = len(skill_dirs)

    # ── 会话 ──
    sessions = list_sessions()
    session_count = len(sessions)
    current_idx = None
    if sid:
        for i, s in enumerate(sessions, 1):
            if s == sid:
                current_idx = i
                break

    # ── 上下文 ──
    msg_count = len(msgs)
    used_tokens = count_messages_tokens(msgs)
    usage_pct = used_tokens / MAX_CONTEXT_TOKENS * 100

    lines = [
        "╔══════════════════════════════╗",
        "║      系统状态概览            ║",
        "╚══════════════════════════════╝",
        "",
        f"  📦 插件    {plugin_count} 个 · {tool_count} 个工具",
        f"  🧠 Skill   {skill_count} 个",
        f"  💬 会话    {session_count} 个{'（当前第 ' + str(current_idx) + '）' if current_idx else ''}",
        "",
        "── 当前会话 ──",
        f"  消息数:    {msg_count} 条",
        f"  Token 占:  {used_tokens:,} / {MAX_CONTEXT_TOKENS:,}（{usage_pct:.1f}%）",
        "",
        "── 配置 ──",
        f"  模型:           {DEEPSEEK_MODEL}",
        f"  工具循环阈值:   {TOOL_LOOP_THRESHOLD:.0%}",
        f"  工具结果预算:   {TOOL_RESULT_BUDGET:.0%}",
        f"  Token/字符:     {TOKEN_PER_CHAR}",
        f"  最大重试:       {MAX_RETRIES} 次",
        "",
        "── 路径 ──",
        f"  工作目录: {os.getcwd()}",
        f"  插件目录: {PLUGINS_DIR}",
    ]

    r.message = "\n".join(lines)


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
