"""指令系统。结构化注册：提供 name/description/usage/handler 即可。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cache_context import CacheContext
from .config import SESSION_DIR
from .session import delete_session, get_session_name, list_sessions

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
    goal: str = ""   # /goal 的原始参数文本,由 main 驱动任务闭环(与 retry 同模式)


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

@builtin("/goal", description="任务闭环:交付 - 独立评分 - 修订,直到达标",
         usage="/goal 目标 || 标准1 | 标准2 [>> 交付文件路径]")
def _cmd_goal(r: CmdResult, _ctx, args, _sid) -> None:
    """指令层只收文本;解析与循环在 outcome.py / main.py(指令系统不养业务逻辑)。"""
    text = " ".join(args).strip()
    if not text or "||" not in text:
        r.message = ("用法: /goal 目标 || 标准1 | 标准2 [>> 交付文件路径]\n"
                     "完成标准不可省略——评分步逐条对照它打分,这是闭环的牙。")
        return
    r.goal = text


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
    r.clear = True
    r.message = "上下文已清除"


@builtin_multi(["/delete", "/rm"], description="删除历史会话", usage="/delete <编号>")
def _cmd_delete(r: CmdResult, _ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /delete <编号>"
        return
    sessions = list_sessions()
    try:
        idx = int(args[0]) - 1
        if 0 <= idx < len(sessions):
            sid = sessions[idx]
            if sid == _sid:
                r.message = "不能删除当前会话，请先 /new 或 /load 切换到其他会话"
                return
            name = get_session_name(sid)
            delete_session(sid)
            r.message = f"已删除会话: {name}"
        else:
            r.message = f"无效编号，共 {len(sessions)} 个会话"
    except ValueError:
        r.message = f"无效编号: {args[0]}"


@builtin_multi(["/sessions", "/ls"], description="列出历史会话")
def _cmd_sessions(r: CmdResult, _ctx, _args, _sid) -> None:
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



# ═══════════════════════════════════════════════════════════════
# 模型指令
# ═══════════════════════════════════════════════════════════════

@builtin("/model", description="查看/切换 LLM 模型", usage="/model [pro]")
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

# ═══════════════════════════════════════════════════════════════
# 上下文诊断指令
# ═══════════════════════════════════════════════════════════════

@builtin("/context", description="上下文诊断：前缀/对话/尾部注入 + 缓存命中率")
def _cmd_context(r: CmdResult, ctx, _args, _sid) -> None:
    """精简版：前缀缓存结构 + 尾部注入清单 + API 命中率。"""
    from .config import MAX_CONTEXT_TOKENS
    from .tools.tokens import count_messages_tokens

    if not hasattr(ctx, 'all'):
        r.message = "需要 CacheContext。"
        return

    all_msgs = ctx.all
    log_msgs = ctx.log
    used_tokens = count_messages_tokens(all_msgs)
    usage_pct = used_tokens / MAX_CONTEXT_TOKENS * 100

    if used_tokens >= int(MAX_CONTEXT_TOKENS * 0.85):
        status = "🔴 紧急"
    elif used_tokens >= int(MAX_CONTEXT_TOKENS * 0.70):
        status = "⚠️ 过载"
    else:
        status = "✅ 正常"

    lines = [
        "══ 上下文诊断 ══",
        "",
        f"  Token:  {used_tokens:,} / {MAX_CONTEXT_TOKENS:,} ({usage_pct:.1f}%)  {status}",
        f"  消息:    {len(all_msgs)} 条",
        "",
        "── 前缀（始终命中）──",
    ]

    # 前缀内容
    for i, m in enumerate(ctx.prefix, 1):
        content = m.get("content", "")
        label = "AGENTS.md" if i == 1 else "运行时机制索引"
        lines.append(f"  [{i}] {label:<16} {len(content):,} 字符")
    lines.append(f"  哈希: {ctx.prefix_hash}")
    roles: dict[str, int] = {}
    for m in log_msgs:
        if m.get("role") != "system":
            roles[m["role"]] = roles.get(m["role"], 0) + 1
    lines.append("")
    lines.append("── 对话体（旧轮命中 / 新轮 miss）──")
    for role in ("user", "assistant", "tool"):
        n = roles.get(role, 0)
        bar = "█" * min(n, 40) if n else "—"
        lines.append(f"  {role:<10} {n:>3} 条  {bar}")

    # 尾部注入
    lines.append("")
    lines.append("── 尾部注入（每轮在末尾 → 必然 miss）──")

    _tail_tags: list[tuple[str, str]] = [
        ("主动进化 · 方向发现", "方向发现"),
        ("未完成的长任务", "长任务续做"),
        ("被动进化发现", "被动反思"),
        ("你拥有以下记忆", "记忆索引"),
        ("pin-", "钉板"),
        ("[提醒] 检索", "提醒检索"),
        ("⏪ 对话归档", "压缩归档"),
    ]

    for tag, label in _tail_tags:
        found = None
        for m in log_msgs:
            if m.get("role") == "system" and tag in (m.get("content") or ""):
                found = m
                break
        if found:
            chars = len(found.get("content", ""))
            lines.append(f"  [✓] {label:<10} {chars:>7,} 字符")
        else:
            lines.append(f"  [ ] {label:<10}      —")

    # API 缓存
    from .llm import get_cache_stats
    cache = get_cache_stats(_sid)
    t = cache.get("total", {}) if isinstance(cache, dict) else {}
    reqs = t.get("requests", 0)
    if reqs > 0:
        prompt = t.get("prompt_tokens", 0)
        hit = t.get("cache_hit_tokens", 0)
        miss = prompt - hit
        hit_pct = hit / prompt * 100 if prompt > 0 else 0
        avg_miss = miss / reqs if reqs > 0 else 0

        lines.append("")
        lines.append("── API 缓存 ──")
        lines.append(f"  请求:    {reqs} 次")
        bar_len = 22
        hit_bars = int(bar_len * hit_pct / 100)
        bar = "█" * hit_bars + "░" * (bar_len - hit_bars)
        lines.append(f"  命中率:  {bar}  {hit_pct:.1f}%")
        lines.append(f"           (命中 {hit:,} / 输入 {prompt:,} tok)")
        lines.append(f"  每轮 miss: ~{avg_miss:,.0f} tok (对话增量 + 尾部注入)")

    r.message = "\n".join(lines)

@builtin("/compact", description="手动压缩上下文：驱逐旧对话换摘要（不必等 65% 自动触发）")
def _cmd_compact(r: CmdResult, ctx, _args, _sid) -> None:
    from .llm import MAX_CONTEXT_TOKENS, _compact_context
    from .tools.tokens import count_messages_tokens

    before = count_messages_tokens(ctx.all)
    _compact_context(ctx, "", force=True)
    after = count_messages_tokens(ctx.all)

    if after >= before:
        r.message = "无可压缩内容（对话太短或已是最简）。"
        return
    freed_pct = (before - after) / MAX_CONTEXT_TOKENS * 100
    r.save = True
    r.message = (
        f"已压缩：{before:,} → {after:,} tokens"
        f"（释放约 {freed_pct:.1f}% 上限空间）"
    )


@builtin("/task", description="查看/清空/归档当前长任务", usage="/task [clear | archive <简述>]")
def _cmd_task(r: CmdResult, _ctx, args, _sid) -> None:
    from .tasks import archive_current, clear_current, read_current

    if not args:
        text = read_current()
        r.message = text or "当前无长任务（tasks/current.md 为空）。"
        return
    sub = args[0].lower()
    if sub == "clear":
        r.message = clear_current()
    elif sub == "archive":
        r.message = archive_current(" ".join(args[1:]))
    else:
        r.message = "用法: /task [clear | archive <简述>]"


# ═══════════════════════════════════════════════════════════════
# 热加载 /reload
# ═══════════════════════════════════════════════════════════════

@builtin("/reload", description="热加载代码改动（指令+工具），无需重启")
def _cmd_reload(r: CmdResult, _ctx, _args, _sid) -> None:
    r.message = "reload 由 main.py 拦截处理，此 handler 仅供 /help 注册。"


# ═══════════════════════════════════════════════════════════════
# 自省 /self — Agent 查看自己的架构、工具、命令、插件
# ═══════════════════════════════════════════════════════════════

@builtin("/self", description="自省：查看自己的架构、工具、命令、插件全景")
def _cmd_self(r: CmdResult, _ctx, _args, _sid) -> None:
    """生成 Agent 自省全景（供 AI 读取，非人类 UI）。"""
    from .tools import get_tools

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
    parts.append("  guard.py          — 自我保护：is_protected() 保护关键文件")
    parts.append("  evolution_runner.py — 自进化运行器：run/state/patch/验证回写")
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
    parts.append("    rag.py          — RAG 索引：rag_index/rag_query/rag_status")
    parts.append("    storm.py        — 去重引擎：同轮工具调用滑动窗口去重")
    parts.append("    lint.py         — 检查引擎：check_project（六维军规检查）")
    parts.append("    self.py         — 自我认知：self（结构化架构+运行时状态）")
    parts.append("    evolve.py       — 进化工具：evolve_query/evolve_validate...")
    parts.append("docs/")
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

    r.message = "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 自跟踪 /stats — Agent 查看自己的工具调用统计
# ═══════════════════════════════════════════════════════════════


# ── 自进化命令 ──

@builtin("/lesson", description="从当前会话提取失败模式，存入记忆（教训学习）",
         usage="/lesson [save]")
def _cmd_lesson(r: CmdResult, ctx, args, _sid) -> None:
    from .lesson import extract_lessons, save_lessons

    if not hasattr(ctx, 'log'):
        r.message = "当前上下文不可用。"
        return

    lessons = extract_lessons(ctx.log)
    if not lessons:
        r.message = "当前会话未发现可提取的失败模式。"
        return

    lines = [f"发现 {len(lessons)} 个可学习的模式：\n"]
    for i, le in enumerate(lessons, 1):
        lines.append(f"  #{i} [{le.fingerprint}] {le.content.split(chr(10))[0].lstrip('#').strip()[:80]}")

    do_save = "save" in args
    if do_save:
        n = save_lessons(lessons)
        lines.append(f"\n已存入 {n} 条策略记忆。下次类似场景会自动提醒。")
        r.save = True
    else:
        lines.append("\n说 '/lesson save' 确认存入记忆。")

    r.message = "\n".join(lines)



@builtin("/evolve", description="启动自进化 runner：后台记录，agent 正常执行任务",
         usage="/evolve <目标描述>")
def _cmd_evolve(r: CmdResult, ctx, args, session_id) -> None:
    """启动自进化 runner，把目标作为普通消息交给 agent。

    agent 不需要知道自己在"进化模式"里——像正常任务一样读代码、
    想方案、改文件、跑测试、提交。runner 在后台默默记录。
    """
    if not args:
        r.message = (
            "用法: /evolve <目标描述>\n"
            "例如:\n"
            "  /evolve 优化文件搜索性能\n"
            "  /evolve 给 read_file 添加缓存命中率统计\n"
            "  /evolve 重构 llm.py 中的错误处理逻辑\n"
        )
        return
    goal = " ".join(args)
    from .evolution_runner import start_evolution_run
    try:
        start = start_evolution_run(goal)
    except RuntimeError as exc:
        r.message = f"进化未启动: {exc}"
        return
    ctx.log.append({"role": "user", "content": goal})
    r.retry = True
    r.save = True
    r.message = start.summary + "\n\nAgent 将从 runner 状态继续推进。按 Esc 可中断。"


@builtin("/fix", description="处理方向发现中的第 N 个改进方向",
         usage="/fix <编号>  （如 /fix 3）")
def _cmd_fix(r: CmdResult, ctx, args, _sid) -> None:
    if not args:
        r.message = "用法: /fix <编号>  （如 /fix 3）。用启动时方向报告查看编号。"
        return
    try:
        n = int(args[0])
    except ValueError:
        r.message = f"无效编号: {args[0]}"
        return

    from .gaps import _make_fix_prompt, get_gap_by_index, get_last_report
    report = get_last_report()
    if report is None or not report.gaps:
        r.message = "暂无方向发现报告，先正常对话一轮让系统启动检测。"
        return
    gap = get_gap_by_index(n)
    if gap is None:
        r.message = f"编号 {n} 超出范围（当前共 {len(report.gaps)} 个方向）。"
        return

    prompt = _make_fix_prompt(gap, n)
    ctx.log.append({"role": "user", "content": prompt})
    r.retry = True
    r.save = True
    r.message = f"已启动方向 #{n}：{gap.detail[:80]}..."


def dispatch(user_input: str, ctx: CacheContext, session_id: str | None) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    if not parts:
        return r
    cmd = parts[0].lower()
    args = parts[1:]

    handler = _handlers.get(cmd)
    if handler:
        handler(r, ctx, args, session_id)
    return r
