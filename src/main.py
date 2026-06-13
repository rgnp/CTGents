import logging
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .cache_context import CacheContext
from .commands import dispatch as dispatch_cmd
from .llm import TokenCallback, clear_interrupt, request_interrupt, run_conversation
from .session import list_sessions, load_session, save_session
from .tools._tool_meta import TOOL_LABELS

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ═══════════════════════════════════════════════════════════════
# Esc 打断监听（Windows msvcrt 后台线程）
# ═══════════════════════════════════════════════════════════════

_esc_listener_active = False


def _start_esc_listener() -> None:
    """启动后台线程监听 Esc 键，用于中断流式回复。"""
    global _esc_listener_active

    import msvcrt  # Windows 专用

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


def _make_memory_context() -> dict | None:
    """读取记忆索引，生成简洁的记忆上下文。"""
    from .tools.memory import get_context
    ctx_str = get_context()
    if not ctx_str:
        return None
    return {"role": "system", "content": ctx_str, "_volatile": True}


logger = logging.getLogger(__name__)


def _make_agents_message() -> dict:
    agents_path = Path(__file__).parent.parent / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8") if agents_path.exists() else "CTGents 编程助手。"
    return {"role": "system", "content": content, "_volatile": True}


def _make_mechanisms_message() -> dict:
    """自动派生「每轮注入的运行时机制」索引，放进缓存前缀——给 agent 环境级自我认知，
    不再对自身架构失忆/编造（曾否认 completion_audit 存在、重造已有机制）。

    内省本模块向 log 注入的非工具机制（_inject_* / _append_volatile_context），取名
    + docstring 首行 → 随代码自动增删，不像手维护的 SYSTEM_MAP 会悄悄烂。只在代码变时
    才变 → 进前缀对缓存命中无损（不像挂尾的记忆索引每轮重算）。
    """
    import inspect
    g = globals()
    names = sorted(n for n in g if n.startswith("_inject_") or n == "_append_volatile_context")
    lines = ["## 你每轮自动注入的运行时机制（自动派生自 main.py，这些确实在跑，不是设想）", ""]
    for n in names:
        doc = (inspect.getdoc(g[n]) or "").splitlines()
        lines.append(f"- `{n}`：{doc[0] if doc else '(无说明)'}")
    return {"role": "system", "content": "\n".join(lines), "_volatile": True}


def _make_date_message() -> dict:
    """今天的日期——放前缀，一天不变，缓存无损。解决 LLM 训练截止日期盲区。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return {"role": "system", "content": f"今天是 {today}。", "_volatile": True}


def _make_prefix_msgs() -> list[dict]:
    """缓存前缀的不可变系统消息：日期 + AGENTS.md（手册）+ 自动派生的运行时机制索引。"""
    return [_make_date_message(), _make_agents_message(), _make_mechanisms_message()]


def _append_volatile_context(ctx: CacheContext) -> None:
    """注入 volatile 上下文：记忆 + 未完成长任务 + 会话钉板（均缓存安全，挂在 log 尾）。"""
    mem_ctx = _make_memory_context()
    if mem_ctx:
        ctx.log.append(mem_ctx)
    from .tasks import make_task_context_message
    task_ctx = make_task_context_message()
    if task_ctx:
        ctx.log.append(task_ctx)
    from .session_pins import render_tail
    pinboard = render_tail()
    if pinboard:
        ctx.log.append({"role": "system", "content": pinboard, "_volatile": True})


def _inject_completion_audit(ctx: CacheContext) -> None:
    """收尾取证自检：剥上一轮的审计提示，若日志里留下"改动晚于绿测"则挂尾提示。

    治"谎报完成"：事实（最近改动 vs 最近绿测）机械供给，"算不算完成"留给 agent。
    走全 log 扫描 → 提示持续到补跑为止；volatile 系统块挂尾，只重算尾、不碰前缀缓存。
    流式下首句已落屏，本提示在下一轮被 agent 看到 → 迟一轮纠正（已与用户交底）。
    """
    ctx.log[:] = [m for m in ctx.log if not m.get("_completion_audit")]
    from .completion_audit import audit_completion
    nudge = audit_completion(ctx.log)
    if nudge:
        ctx.log.append(
            {"role": "system", "content": nudge, "_volatile": True, "_completion_audit": True}
        )


def _inject_citation_audit(ctx: CacheContext) -> None:
    """引用即取证：剥上一轮的提示，若最终回复引用了没取证过的代码文件/标识符则挂尾提示。

    治"编造"的可检查片：引用 path:line 或代码体提及 `标识符` 却全程没在上下文见过
    → 很可能凭印象编的。事实（引用 vs 可见上下文）机械供给，"是不是真编了"留给
    agent；只扫最终回复 → 每轮刷新。传 prefix+log：前缀的派生机制索引/AGENTS.md
    是合法取证源，按索引谈论自身机制不误报。
    """
    ctx.log[:] = [m for m in ctx.log if not m.get("_citation_audit")]
    from .citation_audit import audit_citations
    nudge = audit_citations(ctx.prefix + ctx.log)
    if nudge:
        ctx.log.append(
            {"role": "system", "content": nudge, "_volatile": True, "_citation_audit": True}
        )


_THINKING_NUDGE = (
    "[提醒] 检索 / recall / 读到的内容是线索，不是答案。"
    "问方向 / 取舍 / \"怎么看\"时，先想清楚，给出你的判断 + 理由 + 你会怎么做，"
    "别把搜到的摆出来让用户挑；问事实就直接答、不必长。"
)


def _inject_thinking_stance(ctx: CacheContext) -> None:
    """每轮在 log 尾挂一句"检索命中是线索、不是答案"的常驻提醒（缓存安全）。

    同义 bullet 放 AGENTS.md 前缀实测翻不动"复读"这一根深蒂固的默认（前缀离生成点
    太远）；与两审计同理，行为引导必须挂 log 尾靠 recency 才生效。常驻
    不设门——"这轮算不算开放问题"是判断、不可机械化（auto-plan 的坑）；措辞里的"问
    事实就直接答"让它在事实/动作轮自我收敛。strip-then-append 防累积。
    """
    ctx.log[:] = [m for m in ctx.log if not m.get("_thinking_stance")]
    ctx.log.append(
        {"role": "system", "content": _THINKING_NUDGE,
         "_volatile": True, "_thinking_stance": True}
    )


def process_turn(
    ctx: CacheContext,
    user_input: str,
    on_token: TokenCallback,
    on_tool: Callable[[str, dict], None],
    on_progress: Callable[[], None] | None = None,
    session_id: str = "",
) -> str:
    """一轮对话的数据管线：思考牙 → 预读 → run_conversation → 收尾两审计。

    main 的 REPL 与 test_integration_turn 的交互网共用此唯一定义——网即权威，
    管线一改两边同步，杜绝"测试对着旧副本继续绿"的 drift。I/O（显示/Esc 监听/
    会话保存/打印）由调用方负责，不进此函数。
    """
    # 思考牙：检索命中是线索不是答案，常驻挂尾（前缀劝不动复读，靠 recency 生效）
    _inject_thinking_stance(ctx)
    # 预读优化：用户提到了文件路径，先读入上下文
    pre_msgs = _preread_files(user_input, ctx)
    if pre_msgs:
        contents = "\n\n".join(m["content"] for m in pre_msgs)
        user_input = (
            f"[以下文件已预读，可直接基于其内容回答]\n\n{contents}\n\n"
            f"── 用户问题 ──\n{user_input}"
        )
    reply = run_conversation(
        ctx, user_input, on_token, on_tool,
        on_progress=on_progress, session_id=session_id,
    )
    # 收尾取证自检：未验证的代码改动 + 没取证过的代码引用，挂尾提示（下一轮 agent 见）
    _inject_completion_audit(ctx)
    _inject_citation_audit(ctx)
    return reply


def _handle_goal(ctx: CacheContext, goal_text: str, session_id: str | None) -> str | None:
    """驱动一次任务闭环(/goal):worker 走真实 process_turn 管线,评分隔离在 outcome。

    返回更新后的 session_id。与 r.retry 同模式:指令层只递文本,循环在这里驱动。
    """
    from .outcome import parse_goal, run_outcome
    spec = parse_goal(goal_text)
    if spec is None:
        print("用法: /goal 目标 || 标准1 | 标准2 [>> 交付文件路径](标准不可省略)")
        return session_id
    sid = [session_id]

    def drive(c, text: str) -> str:
        on_token, has_output = _make_display()
        reply = process_turn(
            c, text, on_token, _on_tool,
            on_progress=lambda: sid.__setitem__(0, save_session(c.all, sid[0])),
            session_id=sid[0] or "",
        )
        if has_output():
            print()
        return reply

    _start_esc_listener()
    try:
        result = run_outcome(ctx, spec, drive, on_status=lambda s: print(f"\n{s}"))
        print(f"\n[任务闭环结束] 达标={result.satisfied} 轮数={result.iterations}")
    finally:
        _stop_esc_listener()
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
    print("─" * 40)
    for m in recent:
        role = "You" if m["role"] == "user" else "Agent"
        content = m["content"] or ""
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"{role}: {content}")
    print("─" * 40)


def _make_display() -> tuple[TokenCallback, Callable[[], bool]]:
    """创建流式输出回调。返回 (on_token, has_output)。"""
    started = False

    def on_token(token: str) -> None:
        nonlocal started
        if not started:
            print("Agent: ", end="", flush=True)
            started = True
        print(token, end="", flush=True)

    def has_output() -> bool:
        return started

    return on_token, has_output


def _on_tool(name: str, args: dict) -> None:
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    print(f"  [{label}] {detail}")


def _render_turn_error(e: BaseException) -> tuple[list[str], bool]:
    """分类一轮对话的残余异常（KeyboardInterrupt / SystemExit(0) 已在上游处理）。

    Exception → 友好展示 traceback、不退出循环；其它 BaseException（SystemExit
    非零等）→ 简短提示并退出。返回 (待打印行, 是否 break)。
    旧实现两段都会跑，普通异常被重复报告，故拆成互斥两支。
    """
    if isinstance(e, Exception):
        lines = [f"\n💥 错误: {type(e).__name__}: {e}"]
        lines += [f"   {ln.strip()}" for ln in traceback.format_exception(type(e), e, e.__traceback__)[-5:]]
        lines.append("")
        return lines, False
    return [f"\n  请求失败: {e}\n"], isinstance(e, SystemExit)


# ═══════════════════════════════════════════════════════════════
# 预读优化：用户输入中包含文件路径时，提前读入上下文
# ═══════════════════════════════════════════════════════════════

_FILE_PATH_RE = re.compile(
    r'(?:(?:\.\.?/|[a-zA-Z]:\\|\\\\)?(?:[\w.-]+[/\\])+[\w.-]+\.(?:py|md|txt|json|yaml|yml|toml|cfg|ini|js|ts|html|css|sh|bat|ps1))'
    r'|(?:(?:\.\.?/|[a-zA-Z]:\\|\\\\)?src/[\w./\\-]+\.py)',
)

_PREREAD_MAX = 5       # 最多预读文件数
_PREREAD_MAX_CHARS = 3000  # 单文件最多读取字符


def _preread_files(user_input: str, ctx) -> list[dict]:
    """扫描用户输入中的文件路径，预读到上下文。返回预读的 tool 消息列表。"""
    from .tools.file import _read_cached, _resolve

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

    if not paths:
        return []

    pre_msgs = []
    for p in sorted(paths)[:_PREREAD_MAX]:
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

    # 1. 热加载指令系统
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

    # 2. 热加载内置工具
    try:
        from .tools import reload_tools
        mods = reload_tools()
        loaded_items.append(f"内置工具（{len(mods)} 模块）")
    except Exception as e:
        return False, f"内置工具加载失败: {e}"

    return True, f"已热加载：{'、'.join(loaded_items)}。LLM 下次请求将自动获取最新工具定义。"



# ── 主入口 ──

def _finalize_session(ctx: CacheContext, session_id: str | None) -> list[str]:
    """会话收尾：有回复才落盘 → 会话后反思 → L1 摘要入知识库 → durable 钉板转存。

    反思(tracker.reflect_on_session,被动进化分析层的唯一写入口)在此接线——
    曾挂在 load_session 的 return 之后(不可达)，整层分析管线静默死亡，
    stats/ 下 0 个 reflection 文件实证。反思失败不阻塞退出。
    """
    lines: list[str] = []
    # 只有存在至少一条 assistant 回复时才保存（避免网络错误等空会话落盘）
    if any(m["role"] == "assistant" for m in ctx.all):
        session_id = save_session(ctx.all, session_id)
        lines.append(f"会话已保存: [{session_id}]")
        try:
            from .tracker import reflect_on_session
            if reflect_on_session(session_id):
                lines.append("已写入会话反思（异常发现将在下次启动注入）。")
        except Exception as e:
            logger.warning("会话反思失败（不阻塞退出）: %s", e)
    # ── L1 会话摘要：自动写入 knowledge/sessions/，rag_index_research 可索引 ──
    if any(m["role"] == "assistant" for m in ctx.all):
        try:
            from .session_summary import write_session_summary
            filename = write_session_summary(ctx.all, session_id)
            if filename:
                lines.append(f"已写入会话摘要: knowledge/sessions/{filename}")
        except Exception as e:
            logger.warning("会话摘要失败（不阻塞退出）: %s", e)
    # ── 机械记忆收割：从对话日志自动提取失败模式，不靠 LLM 自觉 ──
    try:
        from .lesson import extract_lessons, save_lessons
        lessons = extract_lessons(ctx.all)
        if lessons:
            n = save_lessons(lessons)
            lines.append(f"已自动收割 {n} 条记忆。")
    except Exception as e:
        logger.warning("记忆收割失败（不阻塞退出）: %s", e)
    # 把 durable 钉板转存进记忆（会话内不漂 → 新会话可 recall）
    from .session_pins import promote_durable
    promoted = promote_durable()
    if promoted:
        lines.append(f"已把 {promoted} 条耐久 pin 转存进记忆。")
    lines.append("退出")
    return lines


def _ensure_git_hooks() -> None:
    """幂等确保 core.hooksPath 指向版本管理的钩子，堵掉"克隆后没钩子"。绝不阻塞启动。"""
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
    messages: list[dict] = []
    ctx: CacheContext | None = None

    if sessions:
        _print_sessions(sessions)
        print()
        choice = input("输入编号加载会话，或直接回车新建: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session_id = sessions[idx]
                messages = load_session(session_id)
                ctx = CacheContext(log_msgs=messages)
                print(f"已加载会话 [{session_id}]，共 {len(ctx)} 条消息")
                _print_recent(ctx.all)
                print()
        except ValueError:
            pass

    # 构建 CacheContext：不可变 prefix + 追加 log
    if ctx is None:
        ctx = CacheContext()
    ctx.rebuild_prefix(_make_prefix_msgs())
    # ── volatile 系统消息（log 末尾，仅追加不修改） ──
    _append_volatile_context(ctx)

    print("Agent 已启动，输入 /help 查看指令列表\n")

    _use_rich_input = sys.stdin.isatty()
    if _use_rich_input:
        from prompt_toolkit import prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("escape")
        def _(event):
            """Esc 清空当前输入行，可重新输入。"""
            buf = event.app.current_buffer
            if buf.text:
                buf.text = ""
            else:
                # 输入行为空时按 Esc 不做任何事（让 prompt 继续等待输入）
                pass

    try:
        while True:
            try:
                user_input = prompt("You: ", key_bindings=kb).strip() if _use_rich_input else input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                # ── 热加载：拦截 /reload，不经过旧 dispatch ──
                if user_input.lower().startswith("/reload"):
                    ok, msg = _reload_dispatch()
                    print(msg)
                    continue

                r = dispatch_cmd(user_input, ctx, session_id)
                if r.message:
                    print(r.message)
                if r.save:
                    session_id = save_session(ctx.all, session_id)
                    print(f"会话已保存: [{session_id}]")
                if r.load:
                    ctx.clear_log()
                    loaded_msgs = load_session(r.load)
                    ctx.log.extend(loaded_msgs)
                    # volatile 上下文（记忆索引/任务/钉板）保存时被过滤，重注
                    _append_volatile_context(ctx)
                    session_id = r.load
                    print(f"已加载会话 [{r.load}]，共 {len(ctx)} 条消息")
                    _print_recent(ctx.all)
                if r.clear:
                    ctx.clear_log()
                    # 重建 prefix 并追加 volatile 上下文（与 session start 一致）
                    ctx.rebuild_prefix(_make_prefix_msgs())
                    if r.save:   # /new: 同时重置 session + 清空会话钉板 + 会话级缓存
                        session_id = None
                        from .session_pins import clear_pins
                        clear_pins()
                        from .tasks import reset_gaps_cache
                        reset_gaps_cache()
                    _append_volatile_context(ctx)
                if r.goal:
                    session_id = _handle_goal(ctx, r.goal, session_id)
                    continue
                if r.retry:
                    last_user = ctx.last_user_content() or ""
                    if last_user:
                        on_token, has_output = _make_display()
                        sid = [session_id]
                        _start_esc_listener()
                        try:
                            run_conversation(
                                ctx, last_user, on_token, _on_tool,
                                on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                                session_id=session_id,
                            )
                        finally:
                            _stop_esc_listener()
                        session_id = sid[0]
                        if has_output():
                            print()
                continue

            try:
                on_token, has_output = _make_display()
                sid = [session_id]
                _start_esc_listener()
                try:
                    process_turn(
                        ctx, user_input, on_token, _on_tool,
                        on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                        session_id=session_id,
                    )
                finally:
                    _stop_esc_listener()
                session_id = sid[0]
                if has_output():
                    print()
            except BaseException as e:
                # ── KeyboardInterrupt：用户主动中断 ──
                if isinstance(e, KeyboardInterrupt):
                    _stop_esc_listener()
                    print("\n[中断]")
                    try:
                        guide = input("指导: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        guide = ""
                    if guide:
                        on_token, has_output = _make_display()
                        sid = [session_id]
                        _start_esc_listener()
                        try:
                            run_conversation(
                                ctx, guide, on_token, _on_tool,
                                on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                                session_id=session_id,
                            )
                        finally:
                            _stop_esc_listener()
                        session_id = sid[0]
                        if has_output():
                            print()
                    continue

                # ── SystemExit(0)：正常退出 ──
                if isinstance(e, SystemExit) and e.code == 0:
                    break

                # 显示错误信息（Exception 友好展示且继续；其它 BaseException 记日志并退出）
                err_lines, should_break = _render_turn_error(e)
                for ln in err_lines:
                    print(ln)
                if not isinstance(e, Exception):
                    logger.error("对话出错: %s", e)
                if should_break:
                    break
    finally:
        for line in _finalize_session(ctx, session_id):
            print(line)


if __name__ == "__main__":
    main()
