"""指令系统。结构化注册：提供 name/description/usage/handler 即可。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from .config import SESSION_DIR
from .cache_context import CacheContext, compute_prefix_hash
from .session import get_session_name, list_sessions, rename_session
from .tools import execute_tool

if TYPE_CHECKING:
    from collections.abc import Callable

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
    handler: Callable[[CmdResult, CacheContext, list[str], str | None], None] | None = None


# 内部注册表
_registry: list[Command] = []
_handlers: dict[str, Callable] = {}


def _add_cmd(cmd: Command) -> None:
    _registry.append(cmd)
    if cmd.handler:
        _handlers[cmd.name] = cmd.handler
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
def _cmd_exit(r: CmdResult, _ctx, _args, _sid) -> None:
    r.exit = True


@builtin_multi(["/help", "/h", "/?"], description="显示指令列表")
def _cmd_help(r: CmdResult, _ctx, _args, _sid) -> None:
    # 按 handler 去重，同 handler 的别名合并显示
    seen: dict[int, list[Command]] = {}
    for cmd in _registry:
        hid = id(cmd.handler)
        seen.setdefault(hid, []).append(cmd)

    lines = ["指令列表：\n"]
    for group in sorted(seen.values(), key=lambda g: g[0].name):
        primary = group[0]
        aliases = [c.name for c in group[1:]]
        name_display = f"{primary.name}（{'、'.join(aliases)}）" if aliases else primary.name
        lines.append(f"  {name_display:<20} {primary.description}")
        if primary.usage:
            lines.append(f"  {'':<20} 用法: {primary.usage}")
    r.message = "\n".join(lines)


@builtin_multi(["/clear", "/c"], description="清除对话上下文")
def _cmd_clear(r: CmdResult, ctx, _args, _sid) -> None:
    ctx.clear_log()
    r.save = True
    r.message = "上下文已清除"


@builtin("/save", description="强制保存当前会话")
def _cmd_save(r: CmdResult, _ctx, _args, _sid) -> None:
    r.save = True
    r.save = True
@builtin("/rename", description="重命名当前会话", usage="/rename <名称>")
def _cmd_rename(r: CmdResult, _ctx, args, sid) -> None:
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
@builtin_multi(["/sessions", "/ls"], description="列出历史会话")
def _cmd_sessions(r: CmdResult, _ctx, _args, _sid) -> None:
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
def _cmd_load(r: CmdResult, _ctx, args, _sid) -> None:
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
def _cmd_new(r: CmdResult, _ctx, _args, _sid) -> None:
    r.save = True
    r.clear = True


@builtin("/pop", description="撤回最后一条对话", usage="/pop [数量]")
def _cmd_pop(r: CmdResult, ctx, args, _sid) -> None:
    n = int(args[0]) if args else 1
    removed = 0
    log = ctx.log
    for _ in range(n):
        while log and log[-1]["role"] != "user":
            log.pop()
        if log and log[-1]["role"] == "user":
            log.pop()
            removed += 1
    r.save = True
    r.message = f"已撤回 {removed} 条对话"

@builtin("/export", description="导出对话为 Markdown", usage="/export [轮数] [文件名]")
def _cmd_export(r: CmdResult, ctx, args, sid) -> None:
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

    messages = list(ctx.all)
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
def _cmd_edit(r: CmdResult, ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /edit <新内容>"
        return
    new_text = " ".join(args)
    log = ctx.log
    while log and log[-1]["role"] != "user":
        log.pop()
    if log and log[-1]["role"] == "user":
        log.pop()
    log.append({"role": "user", "content": new_text})
    r.save = True
    r.retry = True
    r.message = f"已修改: {new_text}"

@builtin("/run", description="直接调用工具", usage="/run <工具名> <参数=值...>")
def _cmd_run(r: CmdResult, _ctx, args, _sid) -> None:
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
def _cmd_plugin(r: CmdResult, _ctx, args, _sid) -> None:
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

@builtin("/reload", description="热加载所有模块 + 工具注册表（改代码后无需重启）")
def _cmd_reload(r: CmdResult, _ctx, _args, _sid) -> None:
    """使用 importlib.reload 热加载所有已加载的 src.* 模块。"""
    import importlib
    import sys

    reloaded = []
    errors = []
    for mod_name in sorted(k for k in sys.modules if k.startswith("src.") and sys.modules[k] is not None):
        try:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")

    # 重新初始化工具注册表
    try:
        from .tools import _init_registry
        _init_registry()
    except Exception as e:
        errors.append(f"tools._init_registry: {e}")

    r.message = f"🔄 热加载完成：{len(reloaded)} 个模块"
    if errors:
        r.message += f"\n  ⚠️ {len(errors)} 个失败"


# ═══════════════════════════════════════════════════════════════
# 模型指令
# ═══════════════════════════════════════════════════════════════

@builtin("/model", description="查看/切换 LLM 模型", usage="/model [flash|pro]")
def _cmd_model(r: CmdResult, _ctx, args, _sid) -> None:
    from .llm import get_current_model_name, list_models, switch_model
    if not args:
        current = get_current_model_name()
        r.message = f"当前模型: {current}\n" + list_models()
        return
    ok, msg = switch_model(args[0])
    r.message = msg
    if ok:
        r.save = True


# ═══════════════════════════════════════════════════════════════
# 状态指令
# ═══════════════════════════════════════════════════════════════

@builtin("/status", description="系统状态概览：插件、会话、配置一览")
def _cmd_status(r: CmdResult, ctx, _args, sid) -> None:
    from .config import (
        MAX_CONTEXT_TOKENS,
        MAX_RETRIES,
        PLUGINS_DIR,
        TOKEN_PER_CHAR,
        TOOL_LOOP_THRESHOLD,
        TOOL_RESULT_BUDGET,
    )
    from .llm import get_current_model_id, get_current_model_name
    from .session import list_sessions
    from .tools.plugin_mgr import _plugins
    from .tools.tokens import count_messages_tokens

    # ── 插件 ──
    plugin_count = len(_plugins)
    tool_count = sum(len(getattr(mod, "TOOLS", [])) for mod in _plugins.values())

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
    all_msgs = ctx.all
    len(all_msgs)
    used_tokens = count_messages_tokens(all_msgs)
    used_tokens / MAX_CONTEXT_TOKENS * 100

    lines = [
        "╔══════════════════════════════╗",
        "║      系统状态概览            ║",
        "╚══════════════════════════════╝",
        "",
        f"  📦 插件    {plugin_count} 个 · {tool_count} 个工具",
        f"  💬 会话    {session_count} 个{'（当前第 ' + str(current_idx) + '）' if current_idx else ''}",
        "",
        "── 当前会话 ──",
        f"  当前模型:     {get_current_model_name()}（{get_current_model_id()}）",
        f"  工具循环阈值:   {TOOL_LOOP_THRESHOLD:.0%}",
        f"  工具结果预算:   {TOOL_RESULT_BUDGET:.0%}",
        f"  Token/字符:     {TOKEN_PER_CHAR}",
        f"  最大重试:       {MAX_RETRIES} 次",
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
# Auto Mode 指令
# ═══════════════════════════════════════════════════════════════

@builtin("/mode", description="查看/切换安全模式", usage="/mode [manual|auto]")
def _cmd_mode(r: CmdResult, _ctx, args, _sid) -> None:
    from .safety import get_mode_summary, set_mode
    if not args:
        r.message = get_mode_summary()
        return
    ok, msg = set_mode(args[0])
    r.message = msg


@builtin_multi(["/trust", "/allow"], description="信任工具（本会话自动放行）", usage="/trust <工具名>")
def _cmd_trust(r: CmdResult, _ctx, args, _sid) -> None:
    from .safety import clear_trust, list_trusted, revoke_trust, trust_tool
    if not args:
        r.message = list_trusted()
        return
    if args[0] == "clear":
        r.message = clear_trust()
    elif args[0] == "list":
        r.message = list_trusted()
    elif args[0].startswith("-"):
        name = args[0][1:]
        r.message = revoke_trust(name)
    else:
        r.message = trust_tool(args[0])


@builtin("/compact", description="手动压缩旧对话，释放 token 空间", usage="/compact [all|keep=N]")
def _cmd_compact(r: CmdResult, ctx, args, _sid) -> None:
    """手动压缩旧对话。"""
    from .llm import _compact_context, _make_brief_summary

    keep = 5  # 默认保留最近 5 轮对话

    if args:
        if args[0] == "all":
            keep = 0
        elif args[0].startswith("keep="):
            try:
                keep = int(args[0].split("=")[1])
                if keep < 0:
                    r.message = "无效参数: keep 必须 >= 0，用法: /compact [all|keep=N]"
                    return
            except ValueError:
                r.message = f"无效参数: {args[0]}，用法: /compact [all|keep=N]"
                return

    original_len = len(ctx)

    if keep == 0:
        # /compact all：全量压缩（话题切换模式）
        # 临时构建扁平列表给 _compact_context
        user_input = "换个话题，重新开始"
        result = _compact_context(ctx.all, user_input)
        # 回写：prefix 取前面的 system 消息，log 取后面的
        prefix_end = sum(1 for m in result if m.get("role") == "system" and "⏪" not in (m.get("content") or ""))
        pfx_count = min(prefix_end, len(ctx.prefix))
        ctx.rebuild_prefix(result[:pfx_count])
        ctx.log[:] = result[pfx_count:]
    else:
        # /compact 或 /compact keep=N：按轮数保留
        log = ctx.log

        # 从后往前数 keep 个 user 消息
        user_found = 0
        retain_start = len(log)
        for i in range(len(log) - 1, -1, -1):
            if log[i].get("role") == "user":
                user_found += 1
                if user_found == keep:
                    retain_start = i
                    break

        if retain_start <= 0 or user_found < keep:
            # 保留起点回退到开头 或 总轮数不够 → 不压缩
            result_log = list(log)
        else:
            # 前半部分压缩为摘要
            to_archive = log[:retain_start]
            brief = _make_brief_summary(to_archive)
            result_log = []
            if brief:
                result_log.append({
                    "role": "system",
                    "content": f"⏪ 对话摘要：{brief}",
                })
            result_log.extend(log[retain_start:])

        ctx.log[:] = result_log

    from .config import MAX_CONTEXT_TOKENS
    from .tools.tokens import count_messages_tokens
    used = count_messages_tokens(ctx.all)

    r.message = (
        f"已压缩：{original_len} → {len(ctx.all)} 条消息\n"
        f"当前 Token: {used:,} / {MAX_CONTEXT_TOKENS:,} ({used/MAX_CONTEXT_TOKENS*100:.1f}%)"
    )
    r.save = True


# ═══════════════════════════════════════════════════════════════
# 上下文诊断指令
# ═══════════════════════════════════════════════════════════════

@builtin("/context", description="查看上下文：token 分布、前缀缓存诊断、API 命中率、压缩状态")
def _cmd_context(r: CmdResult, ctx, _args, _sid) -> None:
    """Context command. Accepts CacheContext (preferred) or legacy list[dict]."""
    from .config import MAX_CONTEXT_TOKENS
    from .tools.tokens import count_messages_tokens

    # Support both CacheContext and legacy flat list
    if hasattr(ctx, 'all'):
        all_msgs = ctx.all
        prefix_hash_val = ctx.prefix_hash
        stats = ctx.stats()
        prefix_msgs = ctx.prefix
        log_msgs = ctx.log
    else:
        all_msgs = ctx
        from .cache_context import compute_prefix_hash
        h, _, _ = compute_prefix_hash(all_msgs)
        prefix_hash_val = h
        stats = None
        prefix_msgs = [m for m in all_msgs if m.get("role") == "system"]
        log_msgs = [m for m in all_msgs if m.get("role") != "system"]
    msg_count = len(all_msgs)
    used_tokens = count_messages_tokens(all_msgs)
    usage_pct = used_tokens / MAX_CONTEXT_TOKENS * 100

    # 按角色统计
    roles: dict[str, int] = {}
    volatile_count = 0
    for m in all_msgs:
        role = m["role"]
        roles[role] = roles.get(role, 0) + 1
        if m.get("_volatile"):
            volatile_count += 1

    # 系统消息中是否有压缩标记（prefix + log system 消息）
    compacted = any("⏪" in (m.get("content") or "") for m in all_msgs if m["role"] == "system")

    # token 预警状态
    warn_70 = int(MAX_CONTEXT_TOKENS * 0.70)
    warn_85 = int(MAX_CONTEXT_TOKENS * 0.85)
    if used_tokens >= warn_85:
        status_tag = "🔴 紧急"
    elif used_tokens >= warn_70:
        status_tag = "⚠️ 过载"
    else:
        status_tag = "✅ 正常"

    lines = [
        "╔══════════════════════════════╗",
        "║      对话上下文诊断          ║",
        "╚══════════════════════════════╝",
        "",
        f"  状态:      {status_tag}",
        f"  Token:     {used_tokens:,} / {MAX_CONTEXT_TOKENS:,} ({usage_pct:.1f}%)",
        f"  消息数:    {msg_count} 条",
        "",
        "── 消息分布 ──",
        f"  system:   {roles.get('system', 0)} 条",
        f"  user:     {roles.get('user', 0)} 条",
        f"  assistant: {roles.get('assistant', 0)} 条",
        f"  tool:     {roles.get('tool', 0)} 条",
        f"  其中 _volatile: {volatile_count} 条（运行时注入，纳入了前缀缓存）",
        "",
        "── 压缩状态 ──",
    ]

    if compacted:
        for m in all_msgs:
            if m["role"] == "system" and "⏪" in (m.get("content") or ""):
                content = m["content"]
                if len(content) > 120:
                    content = content[:120] + "…"
                lines.append("  ✅ 已压缩")
                lines.append(f"  {content}")
                break
    else:
        lines.append("  ❌ 未压缩（70% 触发自动压缩）")

    # ── 三段式结构诊断 ──
    if stats is not None:
        s = stats
        lines.append("")
        lines.append("── 三段式结构 ──")
        lines.append(f"  Prefix: {s['prefix']['messages']} 条 ({s['prefix']['tokens']} token)")
        lines.append(f"  Log:    {s['log']['messages']} 条 ({s['log']['tokens']} token, volatile {s['log']['volatile']})")
        lines.append(f"  Scratch:{s['scratch']['messages']} 条 ({s['scratch']['tokens']} token)")

    # ── 前缀哈希 ──
    lines.append("")
    lines.append("── 前缀缓存 ──")
    lines.append(f"  前缀哈希: {prefix_hash_val}")
    if stats is not None:
        lines.append(f"  前缀内容: {s['prefix']['tokens']} token ({s['prefix']['messages']} 条系统消息)")
    else:
        lines.append(f"  前缀内容: {len(prefix_msgs)} 条系统消息")

    # ── 前缀结构分析 ──
    tag_map = {
        "当前环境": "🌐 环境",
        "当前项目": "📁 项目",
        "你拥有以下记忆": "🧠 记忆",
        "安全模式": "🛡️ 安全",
        "之前对话的摘要": "📝 摘要",
        "对话摘要": "📝 压缩",
        "前一话题": "📝 归档",
    }
    # 分析 prefix + log 中的 system 消息
    # 分析 prefix + log 中的 system 消息
    all_system = list(prefix_msgs) + [m for m in log_msgs if m.get("role") == "system"]
    for m in all_system:
        content = m.get("content", "")
        label = "⚙️ 其他"
        for key, tag in tag_map.items():
            if key in content:
                label = tag
                break
        size = len(content)
        first_line = content.split("\n")[0][:55]
        lines.append(f"    {label}  ({size} 字符)  {first_line}")

    # ── Storm 去重统计 ──
    from .tools.storm import get_storm_stats
    storm = get_storm_stats()
    if storm["hits"] > 0:
        lines.append("")
        lines.append("── Storm 去重 ──")
        lines.append(f"  🔁 拦截重复调用: {storm['hits']} 次")
        lines.append(f"  📐 去重窗口: {storm['window_size']}/8")

    # ── SAFE 并行统计 ──
    from .llm import get_safe_stats
    safe = get_safe_stats()
    if safe["batches"] > 0:
        lines.append("")
        lines.append("── SAFE 并行 ──")
        lines.append(f"  ⚡ 并行批次数: {safe['batches']}")
        lines.append(f"  🔧 并行执行工具数: {safe['parallel_tools']}")
        lines.append(f"  🐌 串行执行工具数: {safe['serial_tools']}")

    # API 缓存命中统计（按模型区分）
    from .llm import get_cache_stats
    cache = get_cache_stats(_sid)
    if isinstance(cache, dict) and "total" not in cache:
        cache = {"models": {}, "total": cache}
    total = cache["total"]
    if total["requests"] > 0:
        lines.append("")
        lines.append("── API 统计（按模型） ──")

        for model_key in ["flash", "pro"]:
            s = cache["models"].get(model_key)
            if not s or s["requests"] == 0:
                continue
            reqs = s["requests"]
            prompt = s["prompt_tokens"]
            completion = s["completion_tokens"]
            hit = s["cache_hit_tokens"]
            miss = s["cache_miss_tokens"]
            hit_ratio = hit / (hit + miss) * 100 if (hit + miss) > 0 else 0
            total_tokens = prompt + completion

            model_emoji = "⚡" if model_key == "flash" else "🧠"
            lines.append(f"  {model_emoji} {model_key.title()}")
            lines.append(f"    请求: {reqs}次  |  输出: {completion:,} tok")
            lines.append(f"    输入: {prompt:,} tok  |  总计: {total_tokens:,} tok")

            if hit + miss > 0:
                bar_len = 16
                hit_bars = int(bar_len * hit_ratio / 100)
                bar = "█" * hit_bars + "░" * (bar_len - hit_bars)
                lines.append(f"    缓存: {bar}  {hit_ratio:.0f}%")
            else:
                lines.append("    缓存: 暂无数据")

        # 总计行
        lines.append("")
        t = total
        t_hit_ratio = t["cache_hit_tokens"] / (t["cache_hit_tokens"] + t["cache_miss_tokens"]) * 100 \
            if (t["cache_hit_tokens"] + t["cache_miss_tokens"]) > 0 else 0
        lines.append(f"  📊 总计: {t['requests']}次请求  "
                      f"{t['prompt_tokens']+t['completion_tokens']:,} token  "
                      f"缓存 {t_hit_ratio:.0f}%")

    non_system_msgs = [m for m in log_msgs if m.get("role") != "system"]
    non_system_msgs = [m for m in log_msgs if m.get("role") != "system"]
    recent_tokens = 0
    for m in non_system_msgs[-6:]:
        content = m.get("content") or ""
        recent_tokens += len(content) * 0.35
    recent_pct = recent_tokens / MAX_CONTEXT_TOKENS * 100 if recent_tokens > 0 else 0

    all_non_system = sum(len(m.get("content") or "") * 0.35 for m in non_system_msgs)
    all_pct = all_non_system / MAX_CONTEXT_TOKENS * 100 if all_non_system > 0 else 0

    lines.append("")
    lines.append("── Token 分布 ──")
    lines.append(f"  旧对话:     {all_pct - recent_pct:.1f}%（可压缩）")
    lines.append(f"  最近对话:   {recent_pct:.1f}%")
    lines.append("")
    lines.append("── 预警阈值 ──")
    lines.append(f"  自动压缩 (70%):  {warn_70:,}")
    lines.append(f"  紧急停止 (85%):  {warn_85:,}")

    r.message = "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 自省 /self — Agent 查看自己的架构、工具、命令、插件
# ═══════════════════════════════════════════════════════════════

@builtin("/self", description="自省：查看自己的架构、工具、命令、插件全景")
def _cmd_self(r: CmdResult, _ctx, _args, _sid) -> None:
    """生成 Agent 自省全景（供 AI 读取，非人类 UI）。"""
    from .tools import get_tools
    from .tools.plugin_mgr import _plugins
    from .tools.mcp import get_connection_summary

    parts: list[str] = []

    # ── 1. 架构概览 ──
    parts.append("## 架构")
    parts.append("src/")
    parts.append("  main.py           — 主循环：接收输入 → dispatch → LLM → 输出")
    parts.append("  commands.py       — 指令系统：/help /save /load /self 等 + dispatch")
    parts.append("  llm.py            — LLM 调用：模型选择、前缀缓存、流式输出")
    parts.append("  config.py         — 配置加载（session 目录、模型配置）")
    parts.append("  cache_context.py  — 三段式上下文 CacheContext（prefix/log/scratch）")
    parts.append("  session.py        — 会话持久化（保存/加载/列表）")
    parts.append("  safety.py         — 安全模式（MANUAL/SAFE/AUTO）+ 拦截规则")
    parts.append("  tools/")
    parts.append("    __init__.py     — 工具注册表、execute_tool() 调度、热加载")
    parts.append("    file.py         — 文件类：read_file/write_file/edit_file_lines...")
    parts.append("    web.py          — 网络类：search_web/read_page")
    parts.append("    exec.py         — 执行类：run_command/run_python")
    parts.append("    code.py         — 代码搜索：grep_code")
    parts.append("    git.py          — Git 类：git_status/git_diff/git_commit/git_push...")
    parts.append("    project.py      — 项目类：scan_project/check_project/generate_agents_md...")
    parts.append("    think.py        — 思考工具：think（策略规划）")
    parts.append("    memory.py       — 记忆工具：remember/recall/forget")
    parts.append("    mcp.py          — MCP 客户端：mcp_connect/mcp_disconnect/mcp_list")
    parts.append("    rag.py          — RAG 索引：rag_index/rag_query/rag_status")
    parts.append("    storm.py        — 去重引擎：同轮工具调用滑动窗口去重")
    parts.append("    lint.py         — 检查引擎：check_project（六维军规检查）")
    parts.append("    discover.py     — 能力发现：discover（扫描所有可用能力）")
    parts.append("    plugin_mgr.py   — 插件管理器：install_plugin/plugin_spec/list_plugins")
    parts.append("  plugins/          — 用户安装的插件目录（热加载）")
    parts.append("docs/")
    parts.append("  roadmap.md        — 路线图")
    parts.append("  AGENTS.md         — AI 操作手册")
    parts.append("tests/              — pytest 测试")
    parts.append("")

    # ── 2. 工具清单（按模块分组） ──
    all_tools = get_tools()
    parts.append(f"## 工具（共 {len(all_tools)} 个）")
    # 按模块分组：从工具名推测分组
    groups: dict[str, list[str]] = {}
    name_to_desc: dict[str, str] = {}
    for t in all_tools:
        fn = t.get("function", {})
        n = fn.get("name", "?")
        desc = fn.get("description", "")[:80]
        name_to_desc[n] = desc
        # 猜测分组名
        if n.startswith("git_"):
            g = "git"
        elif n.startswith("mcp_"):
            g = "mcp"
        elif n.startswith("rag_"):
            g = "rag"
        elif n in ("remember", "recall", "forget"):
            g = "memory"
        elif n in ("search_web", "read_page"):
            g = "web"
        elif n in ("read_file", "read_file_lines", "write_file", "edit_file_lines",
                    "delete_file", "list_files", "count_lines"):
            g = "file"
        elif n in ("run_command", "run_python"):
            g = "exec"
        elif n == "grep_code":
            g = "code"
        elif n in ("scan_project", "check_project", "generate_agents_md", "docs_sync_check"):
            g = "project"
        elif n in ("install_plugin", "plugin_spec", "list_plugins"):
            g = "plugin"
        elif n == "discover":
            g = "discover"
        elif n == "think":
            g = "think"
        else:
            g = "other"
        groups.setdefault(g, []).append(n)

    for gname in sorted(groups.keys()):
        tools_in_group = groups[gname]
        parts.append(f"  [{gname}]")
        for tn in sorted(tools_in_group):
            d = name_to_desc.get(tn, "")
            parts.append(f"    {tn}  — {d}")

    parts.append("")

    # ── 3. 指令清单 ──
    seen: dict[int, list[Command]] = {}
    for cmd in _registry:
        hid = id(cmd.handler)
        seen.setdefault(hid, []).append(cmd)

    parts.append(f"## 指令（共 {len(seen)} 个）")
    for group in sorted(seen.values(), key=lambda g: g[0].name):
        primary = group[0]
        aliases = [c.name for c in group[1:]]
        name_display = f"{primary.name}（{'、'.join(aliases)}）" if aliases else primary.name
        parts.append(f"  {name_display:<24} {primary.description}")

    parts.append("")

    # ── 4. 插件状态 ──
    if _plugins:
        parts.append(f"## 插件（已安装 {len(_plugins)} 个）")
        for pname, pmod in _plugins.items():
            ptools = getattr(pmod, "TOOLS", [])
            pdesc = getattr(pmod, "DESCRIPTION", "")
            parts.append(f"  {pname} — {pdesc} ({len(ptools)} 个工具)")
            for pt in ptools:
                parts.append(f"    {pt.get('name', '?')}")
    else:
        parts.append("## 插件\n  （无）")

    parts.append("")

    # ── 5. MCP 连接 ──
    try:
        connections = get_connection_summary()
        if connections:
            parts.append(f"## MCP 连接（{len(connections)} 个）")
            for cname, ctype in connections.items():
                parts.append(f"  {cname} — {ctype}")
        else:
            parts.append("## MCP 连接\n  （无）")
    except Exception:
        parts.append("## MCP 连接\n  （获取失败）")

    r.message = "\n".join(parts)
def dispatch(user_input: str, ctx: CacheContext, session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = _handlers.get(cmd)
    if handler:
        handler(r, ctx, args, session_id)
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
