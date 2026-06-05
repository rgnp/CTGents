import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from .cache_context import CacheContext
from .commands import dispatch as dispatch_cmd
from .config import SESSION_DIR
from .llm import TokenCallback, TOOL_LABELS, clear_interrupt, request_interrupt, run_conversation
from .session import list_sessions, load_session, save_session

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


def _make_project_context() -> dict | None:
    """生成项目结构感知上下文（不可变前缀成员，跨会话稳定）。"""
    from .tools.project import get_project_context
    context = get_project_context()
    if not context:
        return None
    return {
        "role": "system",
        "content": context,
        "_volatile": True,  # 每次启动重建，不持久化到会话文件
    }

logger = logging.getLogger(__name__)


def _make_agents_message() -> dict:
    agents_path = Path(__file__).parent.parent / "AGENTS.md"
    content = agents_path.read_text(encoding="utf-8") if agents_path.exists() else "CTGents 编程助手。"
    return {"role": "system", "content": content, "_volatile": True}


def _append_volatile_context(ctx: CacheContext) -> None:
    """向 log 末尾追加所有 volatile 系统消息（记忆/安全/RAG/反思）。

    设计原则（对齐 Reasonix cache-first loop）：
      - **绝不在 prefix 中放动态内容** — prefix 必须 byte-stable
      - **log 只追加不修改** — 任何原地编辑都会破坏 DeepSeek 前缀缓存
      - volatile 消息放 log 末尾，不影响主体对话历史的缓存命中
      - 每次会话启动/重载时调用，旧 volatile 保留在历史中但新消息排在最后
    """
    # ── 记忆上下文 ──
    mem_ctx = _make_memory_context()
    if mem_ctx:
        ctx.log.append(mem_ctx)

    # ── RAG 索引状态（移出 prefix，避免索引变→prefix碎→缓存全丢） ──
    try:
        from .tools.rag import get_index_status, _load_doc_index
        rag_info = get_index_status()
        has_code = "未建立" not in rag_info
        has_research = _load_doc_index("research") is not None

        if has_code and has_research:
            hint = "RAG 已就绪：rag_query(scope='code') 搜代码，rag_query(scope='research') 搜论文笔记。研究知识库可用 search_knowledge 或 RAG 语义搜索。"
        elif has_code:
            hint = "RAG 代码索引已就绪。研究知识库可用 search_papers 搜论文、save_note 记笔记。用 rag_index_research 将知识库接入 RAG 语义搜索。"
        elif has_research:
            hint = "RAG 研究索引已就绪。rag_index 可建立代码索引。"
        else:
            hint = ""

        if hint:
            ctx.log.append({"role": "system", "content": hint, "_volatile": True})
    except Exception:
        pass

    # ── 反思摘要 ──
    try:
        from .tools.reflect import get_summary as _reflect_summary
        ref = _reflect_summary()
        if ref:
            ctx.log.append({"role": "system", "content": ref, "_volatile": True})
    except Exception:
        pass


# ── UI 辅助 ──

def _print_sessions(sessions: list[str]) -> None:
    print("历史会话：")
    for i, sid in enumerate(sessions, 1):
        summary_path = os.path.join(SESSION_DIR, sid, "summary.txt")
        preview = ""
        try:
            if os.path.exists(summary_path):
                with open(summary_path, encoding="utf-8") as f:
                    preview = f.read()[:80].replace("\n", " ")
        except Exception:
            pass
        print(f"  [{i}] {sid}  {preview}")


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
    from .tools.file import _resolve, _read_cached

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


def _run_suggest_loop(ctx: CacheContext, session_id: str | None) -> str | None:
    """主动建议 + 修复闭环：扫描工具调用记录，发现问题则询问用户是否修复。"""
    try:
        from .suggest import check as _suggest_check
        tip, repair = _suggest_check()
        if not tip:
            return session_id
        print(f"\n💡 {tip}")
        ans = input("  要修吗？(Y/n) ").strip().lower()
        if ans == "n":
            return session_id
        on_token, has_output = _make_display()
        sid = [session_id]
        _start_esc_listener()
        try:
            run_conversation(
                ctx, repair, on_token, _on_tool,
                on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                session_id=session_id,
            )
        finally:
            _stop_esc_listener()
        if has_output():
            print()
        return sid[0]
    except Exception:
        return session_id


# ── 主入口 ──

def main() -> None:
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
                messages, _summary = load_session(session_id)
                ctx = CacheContext(log_msgs=messages)
                print(f"已加载会话 [{session_id}]，共 {len(ctx)} 条消息")
                _print_recent(ctx.all)
                print()
        except ValueError:
            pass

    # 构建 CacheContext：不可变 prefix + 追加 log
    if ctx is None:
        ctx = CacheContext()
    prefix_msgs = []
    prefix_msgs.append(_make_agents_message())
    proj_ctx = _make_project_context()
    if proj_ctx:
        prefix_msgs.append(proj_ctx)
    ctx.rebuild_prefix(prefix_msgs)
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
                    # 注入一条系统消息，告知 LLM 工具已更新
                    ctx.log.append({
                        "role": "system",
                        "content": "⚠️ 系统已热加载，工具列表已更新。执行 discover 查看最新可用工具。"
                    })
                    continue

                r = dispatch_cmd(user_input, ctx, session_id)
                if r.message:
                    print(r.message)
                if r.save:
                    session_id = save_session(ctx.all, session_id)
                    print(f"会话已保存: [{session_id}]")
                if r.load:
                    ctx.clear_log()
                    loaded_msgs, _summary = load_session(r.load)
                    ctx.log.extend(loaded_msgs)
                    session_id = r.load
                    print(f"已加载会话 [{r.load}]，共 {len(ctx)} 条消息")
                    _print_recent(ctx.all)
                if r.clear:
                    ctx.clear_log()
                    # 重建 prefix 并追加 volatile 上下文（与 session start 一致）
                    prefix_msgs = []
                    prefix_msgs.append(_make_agents_message())
                    proj_ctx = _make_project_context()
                    if proj_ctx:
                        prefix_msgs.append(proj_ctx)
                    ctx.rebuild_prefix(prefix_msgs)
                    if r.save:   # /new: 同时重置 session
                        session_id = None
                    _append_volatile_context(ctx)
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
                        session_id = _run_suggest_loop(ctx, session_id)
                continue

            try:
                # ── 预读优化：用户提到了文件路径，先读入上下文 ──
                pre_msgs = _preread_files(user_input, ctx)
                if pre_msgs:
                    contents = "\n\n".join(m["content"] for m in pre_msgs)
                    user_input = (
                        f"[以下文件已预读，可直接基于其内容回答]\n\n{contents}\n\n"
                        f"── 用户问题 ──\n{user_input}"
                    )

                on_token, has_output = _make_display()
                sid = [session_id]
                _start_esc_listener()
                try:
                    run_conversation(
                        ctx, user_input, on_token, _on_tool,
                        on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                        session_id=session_id,
                    )
                finally:
                    _stop_esc_listener()
                session_id = sid[0]
                if has_output():
                    print()
                session_id = _run_suggest_loop(ctx, session_id)
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

                # 显示错误信息
                if isinstance(e, Exception):
                    print(f"\n💥 错误: {type(e).__name__}: {e}")
                    import traceback
                    tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
                    for line in tb_lines[-5:]:
                        print(f"   {line.strip()}")
                    print()

                # ── 非 Exception 的 BaseException（SystemExit 非零等）──
                logger.error("对话出错: %s", e)
                print(f"\n  请求失败: {e}\n")
                if isinstance(e, SystemExit):
                    break
    finally:
        # 只有存在至少一条 assistant 回复时才保存（避免网络错误等空会话落盘）
        has_response = any(m["role"] == "assistant" for m in ctx.all)
        if has_response:
            session_id = save_session(ctx.all, session_id)
            print(f"会话已保存: [{session_id}]")
        print("退出")


if __name__ == "__main__":
    main()
