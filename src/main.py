import logging
import os
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
# TUI 模式下 Textual 自己管 stdin / Esc，msvcrt 后台线程会和它抢键 → 由 TUI 置位禁用。
_under_tui = False


def _start_esc_listener() -> None:
    """启动后台线程监听 Esc 键，用于中断流式回复。TUI 下禁用（Textual 自管 Esc）。"""
    global _esc_listener_active
    if _under_tui:
        return

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


# 本进程是否真跑过一轮（产生过新内容）。空会话 / 加载后未改动的会话退出时
# 不触发反思/摘要/收割（那是白烧 LLM）。加载/清空会话时复位。
_session_state = {"turn_ran": False}


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
    """自动派生「每轮注入的运行时机制」索引，放进缓存前缀——给 agent 环境级自我认知。"""
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
    """缓存前缀的不可变系统消息（会话开始构建一次，会话内哈希锁死、不变）。

    记忆索引放这里而非尾部：① 它进了缓存前缀，每个请求(含工具循环)都命中、都看得到，
    不再每轮首因排在增长的对话之后而重新 miss；② 会话中途 remember 不会(也不能)改前缀——
    新记忆靠对话上下文带过本会话、落盘后下次会话开始重建前缀时才进索引。记忆是参考资料、
    不靠 recency，进前缀合适。
    """
    msgs = [_make_date_message(), _make_agents_message(), _make_mechanisms_message()]
    mem = _make_memory_context()
    if mem:
        msgs.append(mem)
    return msgs


def _append_volatile_context(ctx: CacheContext) -> None:
    """注入 volatile 上下文：未完成长任务 + 会话钉板（记忆已移入缓存前缀，见 _make_prefix_msgs）。"""
    from .tasks import make_task_context_message
    task_ctx = make_task_context_message()
    if task_ctx:
        ctx.log.append(task_ctx)
    from .session_pins import render_tail
    pinboard = render_tail()
    if pinboard:
        ctx.log.append({"role": "system", "content": pinboard, "_volatile": True})


def _inject_completion_audit(ctx: CacheContext) -> None:
    """收尾取证自检：改动晚于绿测 / 改文件前没先读 → 挂尾提示。"""
    ctx.log[:] = [m for m in ctx.log if not m.get("_completion_audit")]
    from .completion_audit import audit_completion, audit_read_before_write
    nudges = []
    for fn in (audit_completion, audit_read_before_write):
        nudge = fn(ctx.log)
        if nudge:
            nudges.append(nudge)
    if nudges:
        ctx.log.append(
            {"role": "system", "content": "\n".join(nudges),
             "_volatile": True, "_completion_audit": True}
        )

def _inject_citation_audit(ctx: CacheContext) -> None:
    """引用即取证：若最终回复引用了没取证过的代码文件则挂尾提示。"""
    ctx.log[:] = [m for m in ctx.log if not m.get("_citation_audit")]
    from .citation_audit import audit_citations
    nudge = audit_citations(ctx.prefix + ctx.log)
    if nudge:
        ctx.log.append(
            {"role": "system", "content": nudge, "_volatile": True, "_citation_audit": True}
        )


# ── 记忆触发：中→英翻译扩展表（触发专用，补 _TRANSLITERATE 未覆盖的词）──
_MEMORY_TRIGGER_TRANSLATIONS: dict[str, list[str]] = {
    "自进化": ["self evolution", "self-evolution"],
    "进化": ["evolution"],
    "闭环": ["loop", "closed loop"],
    "触发": ["trigger"],
    "越用越懂": ["understanding growth"],
    "路线": ["roadmap"],
    "差距": ["gap"],
    "诊断": ["diagnosis", "gaps"],
    "收割": ["harvest"],
    "反思": ["reflection"],
    "壁纸": ["wallpaper"],
    "宁缺毋滥": ["precision"],
    "错误": ["error", "errors"],
    "系统性": ["systematic"],
    "编辑": ["edit"],
    "调研": ["research", "search"],
    "异步": ["async", "asynchronous"],
}

# 记忆名中属于噪音的部分（日期、纯数字）
_MEMORY_NOISE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d+$")

# 策略型记忆触发时输出的约束模板（按 fingerprint 路由）
_STRATEGY_CONSTRAINT_TEMPLATES: dict[str, str] = {
    "systematic_errors": (
        "[约束] 本轮涉及代码/设计决策——执行前必查三点："
        "① 设计前先 rag_search+search_web 调研方案，不凭大脑知识库；"
        "② 改代码前先 read_file 确认内容，单行→edit_file_lines，多行→write_file；"
        "③ 长任务用 run_async 启动后在后台等，不 poll 循环盯着跑。"
    ),
    "edit_repeated": (
        "[约束] 即将编辑代码：改前必读文件（read_file），"
        "单行替换→edit_file_lines，多行→write_file 完整重写。"
        "行号漂移是 37 次遭遇的最高频失败原因——选错工具就是在同一个坑里原地踏步。"
    ),
    "memory_behavior_gap": (
        "[约束] 记忆→行为闭环：触发了教训记忆时，本轮回复/行动中必须体现对应行为改变。"
        "不是「我知道了」，是「我这次不同了」。"
    ),
}


def _inject_memory_triggers(ctx: CacheContext, user_input: str) -> None:
    """记忆触发：用户输入关键词匹配记忆标题/指纹时，提亮一行提醒。宁缺毋滥。

    两级输出：
    - 策略型（strategy）→ 注入可执行约束模板，强制前置检查
    - 知识型（knowledge/reference）→ 注入一行摘要，需要时 recall 深读
    """
    from .tools.memory import _dir, _split_frontmatter, _tokenize

    # 清除上一轮触发
    ctx.log[:] = [m for m in ctx.log if not m.get("_memory_trigger")]

    mem_dir = _dir()
    if not mem_dir.is_dir():
        return

    # 翻译扩展用户输入
    user_lower = user_input.lower()
    for cn, en_list in _MEMORY_TRIGGER_TRANSLATIONS.items():
        if cn in user_input:
            user_lower += " " + " ".join(en_list)
    user_tokens = _tokenize(user_lower)

    triggers: list[tuple[str, str, str, str, int]] = []  # (name, desc, type, fp, matches)

    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
            if meta.get("severity"):
                continue
            name = meta.get("name", f.stem)
            fp = meta.get("fingerprint", "")
            mem_type = meta.get("type", "")

            # 从 name 和 fingerprint 提取内容词
            keywords: set[str] = set()
            for part in name.replace("-", " ").replace("_", " ").split():
                part = part.strip().lower()
                if _MEMORY_NOISE_RE.match(part):
                    continue
                if len(part) >= 2:
                    keywords.add(part)
            for part in fp.replace("_", " ").split():
                part = part.strip().lower()
                if len(part) >= 2:
                    keywords.add(part)

            if not keywords:
                continue

            matches = sum(
                1 for kw in keywords
                if kw in user_tokens or kw in user_lower
            )
            if matches >= 2:
                desc = meta.get("description", "")
                triggers.append((name, desc[:40] if desc else "", mem_type, fp, matches))
        except Exception:
            continue

    if not triggers:
        return

    triggers.sort(key=lambda x: x[4], reverse=True)
    triggers = triggers[:3]

    # 按类型分层输出
    strategy_hits: list[tuple[str, str, str]] = []  # (name, desc, fp)
    knowledge_hits: list[tuple[str, str]] = []       # (name, desc)

    for name, desc, mem_type, fp, _ in triggers:
        if mem_type == "strategy":
            strategy_hits.append((name, desc, fp))
        else:
            knowledge_hits.append((name, desc))

    # 策略型 → 强约束模板
    for _name, _desc, fp in strategy_hits:
        template = _STRATEGY_CONSTRAINT_TEMPLATES.get(fp)
        if template:
            ctx.log.append({
                "role": "system", "content": template,
                "_volatile": True, "_memory_trigger": True,
            })

    # 知识型 → 摘要提示
    if knowledge_hits:
        parts = []
        for name, desc in knowledge_hits:
            if desc:
                parts.append(f"{name}（{desc}）")
            else:
                parts.append(name)
        line = (
            f"[记忆触发] 以下记忆可能与本轮相关，需要时用 recall 搜索详情："
            f" {', '.join(parts)}"
        )
        ctx.log.append({
            "role": "system", "content": line,
            "_volatile": True, "_memory_trigger": True,
        })


def process_turn(
    ctx: CacheContext,
    user_input: str,
    on_token: TokenCallback,
    on_tool: Callable[[str, dict], None],
    on_progress: Callable[[], None] | None = None,
    session_id: str = "",
) -> str:
    """一轮对话的数据管线：记忆触发 → 预读 → run_conversation → 收尾审计。

    记忆每轮已由 _append_volatile_context 的记忆索引全文注入（约 20 条全给）；
    曾经的 auto_recall（embedding 每轮再搜 top-3 注入）与之重叠、且拖一个未声明的
    80MB sentence-transformers 依赖，已删。需要深挖某条记忆用 recall 工具。
    """
    # 记忆触发：用户输入关键词匹配记忆标题 → 策略型注入约束模板，知识型注入摘要
    _inject_memory_triggers(ctx, user_input)
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
    # 收尾取证自检
    _inject_completion_audit(ctx)
    _inject_citation_audit(ctx)
    return reply


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


def run_agent_turn(ctx: CacheContext, user_input: str,
                   session_id: str | None, *, display=None) -> str | None:
    """主干：一次 agent 驱动。所有入口都走这里，保证不管从哪进、循环都是同一圈。

    对话分支(process_turn：预读→run_conversation→完成/引用审计) →
    若本轮推进了 current.md 则升级到任务分支(run_task_continuation 自主续跑)。

    曾经 /retry 和中断"指导"直接调 run_conversation、绕过 process_turn 的审计与任务
    续跑——同一 agent 从不同入口跑的不是同一个循环。收敛到此函数后各入口一致、闭合。

    display: 输出去向（默认 stdout=REPL；TUI 传写进 widget 的 Display）。循环不变。
    """
    from . import status_bar
    from .task_loop import made_task_progress, run_task_continuation
    from .tasks import read_current as _read_current

    disp = display or _stdout_display()
    _session_state["turn_ran"] = True
    task_before = _read_current()
    sid = [session_id]
    on_token, has_output = disp.make_display()
    status_bar.note_turn_start()
    _start_esc_listener()
    try:
        process_turn(
            ctx, user_input, on_token, disp.on_tool,
            on_progress=lambda: sid.__setitem__(0, save_session(ctx.all, sid[0])),
            session_id=sid[0] or "",
        )
    finally:
        _stop_esc_listener()
    if has_output():
        disp.end_message()

    # 对话分支推进了 current.md → 升级到任务分支
    if made_task_progress(task_before, _read_current()):
        def _task_drive(c, text):
            ot, ho = disp.make_display()
            process_turn(
                c, text, ot, disp.on_tool,
                on_progress=lambda: sid.__setitem__(0, save_session(c.all, sid[0])),
                session_id=sid[0] or "",
            )
            if ho():
                disp.end_message()
        _start_esc_listener()
        try:
            run_task_continuation(ctx, _task_drive, on_status=disp.on_status)
        finally:
            _stop_esc_listener()

    footer = status_bar.note_turn_end()
    if footer:
        disp.on_footer(footer)
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

def _finalize_session(ctx: CacheContext, session_id: str | None) -> list[str]:
    """会话收尾：落盘 → 反思 → 记忆收割 → 用户理解收割 → pin 转存。

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
        session_id = _timed("保存", lambda: save_session(ctx.all, session_id))
        lines.append(f"会话已保存: [{session_id}]")
        try:
            from .tracker import reflect_on_session
            if _timed("反思", lambda: reflect_on_session(session_id)):
                lines.append("已写入会话反思。")
        except Exception as e:
            logger.warning("会话反思失败: %s", e)
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
    ctx.rebuild_prefix(_make_prefix_msgs())
    _append_volatile_context(ctx)

    try:
        if use_tui:
            try:
                from .tui import run_tui
                session_id = run_tui(ctx, session_id, sessions)
            except Exception as e:  # noqa: BLE001  # TUI 起不来 → 回退行式，别让用户卡黑屏
                print(f"[TUI 启动失败，回退行式界面: {type(e).__name__}: {e}]")
                session_id = _run_line_repl(ctx, session_id)
        else:
            session_id = _run_line_repl(ctx, session_id)
    finally:
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
                session_id = save_session(ctx.all, session_id)
                print(f"会话已保存: [{session_id}]")
            if r.load:
                ctx.clear_log()
                loaded_msgs = load_session(r.load)
                ctx.log.extend(loaded_msgs)
                _append_volatile_context(ctx)
                session_id = r.load
                from . import status_bar
                status_bar.reset()  # 切会话复位 Δmiss 基线
                _session_state["turn_ran"] = False  # 加载未改动则退出不收割
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
                _append_volatile_context(ctx)
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
