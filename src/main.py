from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .cache_context import CacheContext
from .commands import dispatch as dispatch_cmd
from .session import list_sessions, load_prefix, load_session, save_prefix, save_session

# 重模块（llm→openai/tools ~1.1s、tools._tool_meta→trafilatura/tavily）延迟到使用处 import，
# 让 `import src.main`（程序入口）从 ~1.3s 降到 ~0.2s → TUI 开屏秒显。第一轮真用时再加载。
if TYPE_CHECKING:
    from .llm import TokenCallback

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ═══════════════════════════════════════════════════════════════
# Esc 打断监听（Windows msvcrt 后台线程）
# ═══════════════════════════════════════════════════════════════

_esc_listener_active = False
# TUI 模式下 Textual 自己管 stdin / Esc，msvcrt 后台线程会和它抢键 → 由 TUI 置位禁用。
_under_tui = False


def _start_esc_listener() -> None:
    """启动后台线程监听 Esc 键，用于中断流式回复。TUI 下禁用（Textual 自管 Esc）。"""
    global _esc_listener_active
    if _under_tui:
        return

    import msvcrt  # Windows 专用

    from .llm import clear_interrupt, request_interrupt

    _esc_listener_active = True
    clear_interrupt()

    def _listen():
        while _esc_listener_active:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'\x1b':  # Esc 键
                    request_interrupt()
                    return
            time.sleep(0.05)  # 50ms 轮询，不忙等

    t = threading.Thread(target=_listen, daemon=True)
    t.start()


def _stop_esc_listener() -> None:
    """停止 Esc 监听线程。"""
    global _esc_listener_active
    _esc_listener_active = False


# 本进程是否真跑过一轮（产生过新内容）。空会话 / 加载后未改动的会话退出时
# 不触发反思/摘要/收割（那是白烧 LLM）。加载/清空会话时复位。
_session_state = {"turn_ran": False, "task_reminded": False}


def _make_memory_context() -> dict | None:
    """读取记忆索引，生成简洁的记忆上下文。"""
    from .tools.memory import get_context
    ctx_str = get_context()
    if not ctx_str:
        return None
    return {"role": "system", "content": ctx_str, "_volatile": True, "_label": "记忆索引"}


logger = logging.getLogger(__name__)


def _make_agents_message() -> dict:
    agents_path = Path(__file__).parent.parent / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8") if agents_path.exists() else "CTGents 编程助手。"
    return {"role": "system", "content": content, "_volatile": True, "_label": "AGENTS.md"}


# _make_mechanisms_message（自动派生「每轮注入的运行时机制」索引）已删除：它索引的
# 是 _inject_* / _append_volatile_context 这些挂尾注入器，随挂尾机制整体删除已无对象可索引。


def _make_ambitions_message() -> dict | None:
    """长期目标（tasks/ambitions.md）放进缓存前缀——它 session 稳定、是弱方向参考。

    曾在挂尾(make_task_context_message)，但任何挂尾内容都让"对话"不再是请求的输入结束
    位置，轮首只能命中脆弱内部单元、空闲~40s 即被服务端淘汰、整段对话重 miss。挪进前缀
    (会话开始建一次、冻结)后对话重新成为可靠的输入结束单元。见 [[ctgents-context-cache]]。
    """
    from .tasks import has_ambitions, read_ambitions
    if not has_ambitions():
        return None
    content = ("📋 你们共同的长期目标（tasks/ambitions.md），所有决策的弱方向参考：\n\n"
               + read_ambitions())
    return {"role": "system", "content": content, "_volatile": True, "_label": "长期目标"}


def _make_reflections_message() -> dict | None:
    """被动进化反思放进缓存前缀——session 稳定（反思在会话边界写、本会话内不变）。

    曾在挂尾(make_task_context_message)，是 ambitions 移走后最后一个 session 稳定的挂尾项；
    任何挂尾内容都让"对话"成脆弱内部单元、负载尖峰时偶发整段重 miss。挪进前缀消灭这条。
    """
    from .tasks import build_reflections_text
    text = build_reflections_text()
    if not text:
        return None
    return {"role": "system", "content": text, "_volatile": True, "_label": "被动进化反思"}


def _make_prefix_msgs() -> list[dict]:
    """缓存前缀的不可变系统消息（会话开始构建一次，会话内哈希锁死、不变）。

    缓存命中已判定为 DeepSeek 服务端问题、不再是约束（见 [[ctgents-context-cache]]），
    故把 session 稳定的器官接回前缀：AGENTS.md + 记忆索引(轴①越用越懂你) + 长期目标
    ambitions + 被动进化反思 reflections。这些都在会话边界产生/会话内不变，放冻结前缀里
    既送达模型、又不破坏纯追加（per-turn 动态的任务上下文/审计不在这里，走 append-only）。
    每个 _make_* 缺数据时返回 None，按序过滤——空记忆/无任务的会话前缀自动退回只剩 AGENTS.md。
    """
    makers = (
        _make_agents_message,
        _make_memory_context,
        _make_ambitions_message,
        _make_reflections_message,
    )
    return [m for m in (make() for make in makers) if m]


def _save_ctx(ctx: CacheContext, session_id: str | None) -> str:
    """落盘会话日志 + 冻结前缀，返回（可能新生成的）session_id。

    前缀单独存（它整段是 _volatile、被 save_session 过滤掉），保证同一会话跨重启
    复用同一份前缀字节、服务端缓存保持热（见 session.save_prefix）。
    """
    sid = save_session(ctx.all, session_id)
    if ctx.prefix:
        save_prefix(sid, ctx.prefix)
    return sid


def _apply_prefix(ctx: CacheContext, session_id: str | None) -> None:
    """为 ctx 装上前缀：优先复用该会话落盘的冻结前缀；没有（新会话 / 旧存档）才
    从当前磁盘现生成。这样重载已有会话不再因 AGENTS.md / 记忆变动而前缀作废、命中暴跌；
    记忆 / AGENTS.md 的更新只在新建会话（下次现生成前缀）时生效——刻意如此。
    """
    saved = load_prefix(session_id) if session_id else None
    ctx.rebuild_prefix(saved if saved else _make_prefix_msgs())


# 旧的"挂尾"注入器（_append_volatile_context / _inject_* 把 volatile system 消息摆在
# payload 末尾）已整体删除——那是破坏前缀缓存的根因。④可信审计已按 append-only 重新接回
# （见 _run_post_turn_audits：nudge 追加进永久 log、不挂尾）。记忆触发逻辑当时连同测试
# 一起删除，未重建。详见 [[ctgents-context-cache]]。


def process_turn(
    ctx: CacheContext,
    user_input: str,
    on_token: TokenCallback,
    on_tool: Callable[[str, dict], None],
    on_progress: Callable[[], None] | None = None,
    session_id: str = "",
    on_reasoning: Callable[[str], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
) -> str:
    """一轮对话的数据管线 → run_conversation（工具循环 + 流式 + 压缩）。"""
    from .llm import run_conversation
    return run_conversation(
        ctx, user_input, on_token, on_tool,
        on_progress=on_progress, session_id=session_id, on_reasoning=on_reasoning,
        on_tool_result=on_tool_result)


def _stdout_display():
    """REPL 默认输出去向：stdout。封装成 Display 让 run_agent_turn 与 TUI 共用同一咽喉。

    保留 _make_display / _on_tool 两个模块级名字（test_main 给它们打桩），默认路径仍走它们。
    """
    from . import ui
    return ui.Display(
        make_display=_make_display,
        on_tool=_on_tool,
        on_status=print,
        on_footer=lambda f: (print(f) if sys.stdin.isatty() else None),
        end_message=print,
    )


def _drive_turn(ctx: CacheContext, user_input: str, disp, sid: list) -> None:
    """跑一整轮 process_turn 管线 + 显示/Esc 中断/footer。sid 是单元素 list（出参）。

    run_agent_turn 与任务自主续跑共用此咽喉——保证不管谁驱动，循环都是同一圈、
    drift 闭合（见 [[project_ctgents]] d4f9a47）。
    """
    from . import status_bar
    _session_state["turn_ran"] = True
    on_token, has_output = disp.make_display()
    status_bar.note_turn_start()
    _start_esc_listener()
    try:
        process_turn(
            ctx, user_input, on_token, disp.on_tool,
            on_progress=lambda: sid.__setitem__(0, _save_ctx(ctx, sid[0])),
            session_id=sid[0] or "",
            on_reasoning=disp.on_reasoning,
            on_tool_result=getattr(disp, "on_tool_result", None),
        )
    finally:
        _stop_esc_listener()
    if has_output():
        disp.end_message()
    footer = status_bar.note_turn_end()
    if footer:
        disp.on_footer(footer)


def _run_post_turn_audits(ctx: CacheContext, disp) -> None:
    """④可信：轮末取证自检（谎报完成 / 编造引用 / 不读就改）。

    append-only：命中的 nudge 作为**非 volatile** system 消息追加进 log——send() 只丢
    volatile system、保留它，故下一轮模型看得到、能自纠；永不挂尾、永不原地改历史
    （这是用户定的 canonical，区别于 per-turn 动态的任务上下文）。去重：同一条 nudge
    只在 log 里尚无同文 _audit 消息时追加一次——staleness 类 nudge 条件不变会每轮重复
    返回，不去重会堆积刷屏。命中即同时打印给用户（你来抓 agent 谎报）。
    """
    from .citation_audit import audit_citations
    from .completion_audit import (
        audit_completion,
        audit_memory_consult,
        audit_quality_check,
        audit_read_before_write,
    )

    log = ctx.all
    nudges = [a(log) for a in (
        audit_completion, audit_citations, audit_read_before_write,
        audit_memory_consult, audit_quality_check)]
    existing = {m.get("content") for m in ctx.log if m.get("_audit")}
    # 每条 nudge 单独成一条 _audit 消息——去重按单条比对（join 后整体比对会让
    # 单条 nudge 永远 != 历史里的 join 文本、每轮重复追加）。
    fresh = [n for n in nudges if n and n not in existing]
    if not fresh:
        return
    for n in fresh:
        ctx.log.append({"role": "system", "content": n, "_audit": True})
    disp.on_status("\n\n".join(fresh))


def run_agent_turn(ctx: CacheContext, user_input: str,
                   session_id: str | None, *, display=None) -> str | None:
    """主干：一次 agent 驱动。所有入口都走这里，保证不管从哪进、循环都是同一圈。

    一轮 = 一次 _drive_turn（对话）+ 任务自主续跑（若这轮真推进了 current.md，自主驱动
    后续步骤直到 agent 自己停）+ 轮末 ④可信审计（append-only）。

    display: 输出去向（默认 stdout=REPL；TUI 传写进 widget 的 Display）。循环不变。
    """
    from .task_loop import made_task_progress, run_task_continuation
    from .tasks import read_current

    disp = display or _stdout_display()
    sid = [session_id]

    # 可恢复：会话首轮若有未完成长任务，append-only 注入一次提醒（恢复跨会话/重启后的"注意力"）。
    if not _session_state["task_reminded"]:
        _session_state["task_reminded"] = True
        from .tasks import resume_reminder
        rem = resume_reminder()
        if rem:
            ctx.log.append({"role": "system", "content": rem, "_resume": True})
            disp.on_status(rem)

    before_task = read_current()
    _drive_turn(ctx, user_input, disp, sid)

    # 任务自主续跑：这一轮 agent 真推进了 current.md（参与了任务）→ 自主驱动后续步骤。
    # 但若 agent 这轮已显式喊停（task_done/need_user），尊重它、不再续跑。
    # 续跑内部"有未完成就继续"，停由显式信号/卡死保险决定（见 run_task_continuation）。
    if ctx.control_signal is None and made_task_progress(before_task, read_current()):
        run_task_continuation(
            ctx,
            lambda c, prompt: _drive_turn(c, prompt, disp, sid),
            on_status=disp.on_status,
        )

    _run_post_turn_audits(ctx, disp)
    return sid[0]


# ── UI 辅助 ──

def _print_sessions(sessions: list[str]) -> None:
    print("历史会话：")
    for i, sid in enumerate(sessions, 1):
        print(f"  [{i}] {sid}")


def _print_recent(messages: list[dict], count: int = 4) -> None:
    """回显最近 N 轮对话历史。"""
    exchanges: list[dict] = [m for m in messages if m["role"] != "system"]
    if not exchanges:
        return
    recent = exchanges[-min(count * 2, len(exchanges)):]
    pairs: list[tuple[str, str]] = []
    for m in recent:
        role = "You" if m["role"] == "user" else "Agent"
        content = m["content"] or ""
        if len(content) > 200:
            content = content[:200] + "..."
        pairs.append((role, content))
    from . import ui
    ui.recent_history(pairs)


def _make_display() -> tuple[TokenCallback, Callable[[], bool]]:
    """创建流式输出回调。返回 (on_token, has_output)。样式见 ui.agent_display。"""
    from . import ui
    return ui.agent_display()


def _on_tool(name: str, args: dict) -> None:
    from . import ui
    from .tools._tool_meta import TOOL_LABELS
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    ui.tool_call(label, detail)


def _render_turn_error(e: BaseException) -> tuple[list[str], bool]:
    """分类一轮对话的残余异常。"""
    if isinstance(e, Exception):
        lines = [f"\n💥 错误: {type(e).__name__}: {e}"]
        lines += [f"   {ln.strip()}" for ln in traceback.format_exception(type(e), e, e.__traceback__)[-5:]]
        lines.append("")
        return lines, False
    return [f"\n  请求失败: {e}\n"], isinstance(e, SystemExit)


# ═══════════════════════════════════════════════════════════════
# 预读优化
# ═══════════════════════════════════════════════════════════════

_FILE_PATH_RE = re.compile(
    r'(?:(?:\.\.?/|[a-zA-Z]:\\|\\\\)?(?:[\w.-]+[/\\])+[\w.-]+\.(?:py|md|txt|json|yaml|yml|toml|cfg|ini|js|ts|html|css|sh|bat|ps1))'
    r'|(?:(?:\.\.?/|[a-zA-Z]:\\|\\\\)?src/[\w./\\-]+\.py)',
)

_PREREAD_MAX = 5
_PREREAD_MAX_CHARS = 3000


def _preread_files(user_input: str, ctx) -> list[dict]:
    """扫描用户输入中的文件路径，预读到上下文。"""
    paths = _collect_preread_paths(user_input)
    if not paths:
        return []
    return _build_preread_messages(paths)


def _collect_preread_paths(user_input: str) -> list:
    from .tools.file import _resolve
    paths = set()
    for m in _FILE_PATH_RE.finditer(user_input):
        raw = m.group(0).strip().rstrip(".,;:!?\"'")
        if len(raw) < 4:
            continue
        try:
            p = _resolve(raw)
            if p.exists() and p.is_file():
                paths.add(p)
        except Exception:
            continue
        if len(paths) >= _PREREAD_MAX:
            break
    return sorted(paths)[:_PREREAD_MAX]


def _build_preread_messages(paths: list) -> list[dict]:
    from .tools.file import _read_cached
    pre_msgs = []
    for p in paths:
        content = _read_cached(p)
        if content is None:
            continue
        if len(content) > _PREREAD_MAX_CHARS:
            content = content[:_PREREAD_MAX_CHARS] + (
                f"\n\n[预读截断：文件 {len(content)} 字符，仅显示前 {_PREREAD_MAX_CHARS} 字符]"
            )
        pre_msgs.append({
            "role": "tool",
            "tool_call_id": f"preread_{hash(str(p))}",
            "content": f"[预读] {p}\n{content}",
            "_tool_name": "read_file",
        })
        print(f"  📖 预读: {p}")
    return pre_msgs


def _reload_dispatch():
    """全量热加载：指令系统 + 内置工具 + 插件，无需重启。"""
    global dispatch_cmd

    loaded_items = []

    for k in list(sys.modules.keys()):
        if k == 'src.commands':
            del sys.modules[k]
            break
    try:
        import src.commands
        dispatch_cmd = src.commands.dispatch
        loaded_items.append("指令系统")
    except Exception as e:
        return False, f"指令系统加载失败: {e}"

    try:
        from .tools import reload_tools
        mods = reload_tools()
        loaded_items.append(f"内置工具（{len(mods)} 模块）")
    except Exception as e:
        return False, f"内置工具加载失败: {e}"

    return True, f"已热加载：{'、'.join(loaded_items)}。"


# ── 主入口 ──

# 会话关闭时的收割（教训 / 用户理解 / 项目知识）总开关。默认关：每次关闭都全量
# LLM 重写记忆档案，既烧 LLM 调用、又 churn 记忆索引 → 下次新建会话前缀变动。
# 记忆改为「用出来的」——靠 agent 显式 remember，不靠每次关闭自动收割。
# 设 CTG_HARVEST_ON_CLOSE=1 恢复关闭时收割。
_HARVEST_ON_CLOSE = os.getenv("CTG_HARVEST_ON_CLOSE", "0").strip().lower() not in ("", "0", "false", "no")


def _finalize_session(ctx: CacheContext, session_id: str | None) -> list[str]:
    """会话收尾：落盘 → 反思 →（收割默认关，见 _HARVEST_ON_CLOSE）→ pin 转存。

    本进程没真跑过一轮（空会话 / 加载后未改动就退出）则直接退出，不触发任何
    反思/摘要/收割——那些是 LLM 调用，对没新内容的会话纯属白烧。
    """
    lines: list[str] = []
    if not _session_state["turn_ran"]:
        return ["退出"]
    timings: list[tuple[str, float]] = []

    def _timed(label: str, fn):
        t0 = time.perf_counter()
        try:
            return fn()
        finally:
            timings.append((label, time.perf_counter() - t0))

    if any(m["role"] == "assistant" for m in ctx.all):
        session_id = _timed("保存", lambda: _save_ctx(ctx, session_id))
        lines.append(f"会话已保存: [{session_id}]")
        try:
            from .tracker import reflect_on_session
            if _timed("反思", lambda: reflect_on_session(session_id)):
                lines.append("已写入会话反思。")
        except Exception as e:
            logger.warning("会话反思失败: %s", e)
    if _HARVEST_ON_CLOSE:
        try:
            from .lesson import extract_lessons, save_lessons
            lessons = _timed("教训", lambda: extract_lessons(ctx.all))
            if lessons:
                n = save_lessons(lessons)
                lines.append(f"已自动收割 {n} 条记忆。")
        except Exception as e:
            logger.warning("记忆收割失败: %s", e)
        if any(m["role"] == "assistant" for m in ctx.all):
            # 用户档案 + 项目知识都是阻塞 LLM 调用。两者各写不同记忆文件，但 _remember
            # 末尾会重建共享索引 MEMORY.md（并发写同一文件会互相截断）——故 LLM 调用并发跑、
            # 落盘串行做：把退出等待从 串行(64+44s) 砍到 并发(≈max)。
            import concurrent.futures as _cf

            from .project_model import harvest_project_knowledge, save_project_knowledge
            from .user_model import harvest_user_profile, save_user_profile
            log_all = ctx.all

            def _harvest_both() -> tuple[str | None, str | None]:
                with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                    fu = ex.submit(harvest_user_profile, log_all)
                    fp = ex.submit(harvest_project_knowledge, log_all)
                    return fu.result(), fp.result()

            user_body = proj_body = None
            try:
                user_body, proj_body = _timed("收割(并发)", _harvest_both)
            except Exception as e:
                logger.warning("收割失败: %s", e)
            if user_body and save_user_profile(user_body):
                lines.append("已更新用户理解档案（下次会话自动注入）。")
            if proj_body and save_project_knowledge(proj_body):
                lines.append("已更新项目知识档案（下次会话索引可见，recall 取详情）。")
    from .session_pins import promote_durable
    promoted = _timed("pin转存", promote_durable)
    if promoted:
        lines.append(f"已把 {promoted} 条耐久 pin 转存进记忆。")
    if timings:
        slow = sorted(timings, key=lambda kv: kv[1], reverse=True)
        brief = " ".join(f"{k}{v:.1f}s" for k, v in slow if v >= 0.05)
        lines.append(f"收尾耗时: {brief or '全部<0.05s'}")
    lines.append("退出")
    return lines


def _ensure_git_hooks() -> None:
    try:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from scripts.install_hooks import ensure_installed
        ensure_installed()
    except Exception:
        pass


def main() -> None:
    _ensure_git_hooks()
    sessions = list_sessions()
    session_id: str | None = None
    ctx: CacheContext | None = None

    _tui_enabled = os.getenv("CTG_TUI", "1").strip().lower() not in ("0", "false", "no")
    use_tui = _tui_enabled and sys.stdin.isatty() and sys.stdout.isatty()

    # 行式才在启动前用 CLI 选会话；TUI 把选会话搬进界面内（避免先闪一段老命令行）。
    if not use_tui and sessions:
        _print_sessions(sessions)
        print()
        choice = input("输入编号加载会话，或直接回车新建: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session_id = sessions[idx]
                ctx = CacheContext(log_msgs=load_session(session_id))
                print(f"已加载会话 [{session_id}]，共 {len(ctx)} 条消息")
                _print_recent(ctx.all)
                print()
        except ValueError:
            pass

    if ctx is None:
        ctx = CacheContext()

    def _ensure_ctx_ready() -> None:
        # 行式 / 回退路径在此初始化；TUI 路径把它推迟到开屏后台做（界面秒显）。
        if not ctx.prefix:
            _apply_prefix(ctx, session_id)  # 加载的会话复用其冻结前缀；新会话现生成

    try:
        if use_tui:
            try:
                from .tui import run_tui
                session_id = run_tui(ctx, session_id, sessions)  # ctx 初始化在开屏后台做
            except Exception as e:  # noqa: BLE001  # TUI 起不来 → 回退行式，别让用户卡黑屏
                print(f"[TUI 启动失败，回退行式界面: {type(e).__name__}: {e}]")
                _ensure_ctx_ready()
                session_id = _run_line_repl(ctx, session_id)
        else:
            _ensure_ctx_ready()
            session_id = _run_line_repl(ctx, session_id)
    finally:
        _ensure_ctx_ready()   # 收尾前兜底：TUI 未及初始化也保证 prefix 在
        for line in _finalize_session(ctx, session_id):
            print(line)


def _run_line_repl(ctx: CacheContext, session_id: str | None) -> str | None:
    """行式 REPL（TUI 关闭或起不来时的兜底）。返回最终 session_id，收尾由 main 统一做。"""
    from . import ui
    ui.banner("CTGents · 输入 /help 查看指令列表")

    _use_rich_input = sys.stdin.isatty()
    if _use_rich_input:
        from prompt_toolkit import prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("escape")
        def _(event):
            buf = event.app.current_buffer
            if buf.text:
                buf.text = ""
            else:
                pass

    _pending_job_notices: list[str] = []
    while True:
        # 收割已完成的后台作业 → 打印通知 + 缓存到下条用户消息（取代 agent poll 忙等）
        try:
            from .tools.exec import drain_finished_jobs
            for _notice in drain_finished_jobs():
                print(f"\n{_notice}\n")
                _pending_job_notices.append(_notice)
        except Exception:
            pass
        try:
            if _use_rich_input:
                from . import status_bar
                status_bar.refresh(ctx, session_id)
                user_input = prompt(
                    ui.prompt_message(), key_bindings=kb, bottom_toolbar=status_bar.text
                ).strip()
            else:
                user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if user_input.lower().startswith("/reload"):
                ok, msg = _reload_dispatch()
                print(msg)
                continue

            r = dispatch_cmd(user_input, ctx, session_id)
            if r.message:
                print(r.message)
            if r.save:
                session_id = _save_ctx(ctx, session_id)
                print(f"会话已保存: [{session_id}]")
            if r.load:
                ctx.clear_log()
                loaded_msgs = load_session(r.load)
                ctx.log.extend(loaded_msgs)
                _apply_prefix(ctx, r.load)  # 复用该会话冻结前缀，重载不塌命中
                session_id = r.load
                from . import status_bar
                status_bar.reset()  # 切会话复位 Δmiss 基线
                _session_state["turn_ran"] = False  # 加载未改动则退出不收割
                _session_state["task_reminded"] = False  # 切会话→新会话首轮重提醒未完成任务
                print(f"已加载会话 [{r.load}]，共 {len(ctx)} 条消息")
                _print_recent(ctx.all)
            if r.clear:
                ctx.clear_log()
                ctx.rebuild_prefix(_make_prefix_msgs())
                if r.save:
                    session_id = None
                    from .session_pins import clear_pins
                    clear_pins()
                    from .tasks import reset_gaps_cache
                    reset_gaps_cache()
                    from . import status_bar
                    status_bar.reset()  # 清空会话复位 Δmiss 基线
                    _session_state["turn_ran"] = False  # 清空后空会话退出不收割
                    _session_state["task_reminded"] = False  # 清空后允许再次提醒
            if r.retry:
                last_user = ctx.last_user_content() or ""
                if last_user:
                    session_id = run_agent_turn(ctx, last_user, session_id)
            continue

        if _pending_job_notices:
            user_input = (
                "【后台作业完成】\n" + "\n\n".join(_pending_job_notices)
                + "\n\n【用户消息】\n" + user_input
            )
            _pending_job_notices.clear()
        try:
            session_id = run_agent_turn(ctx, user_input, session_id)
            # ── 暂停菜单：中断后可选重试 ──
            if getattr(ctx, "control_signal", None) == "interrupted":
                print("\n⏸️ 已暂停，现场已保存。")
                print("  回车或输入 → 开始新输入")
                print("  r          → 重试上一条消息")
                choice = input("选择: ").strip().lower()
                if choice == "r":
                    session_id = run_agent_turn(ctx, user_input, session_id)
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                _stop_esc_listener()
                print("\n[中断]")
                try:
                    guide = input("指导: ").strip()
                except (EOFError, KeyboardInterrupt):
                    guide = ""
                if guide:
                    session_id = run_agent_turn(ctx, guide, session_id)
                continue

            if isinstance(e, SystemExit) and e.code == 0:
                break

            err_lines, should_break = _render_turn_error(e)
            for ln in err_lines:
                print(ln)
            if not isinstance(e, Exception):
                logger.error("对话出错: %s", e)
            if should_break:
                break
    return session_id


if __name__ == "__main__":
    main()
