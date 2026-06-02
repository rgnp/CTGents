import logging
import os
import platform
import sys
import threading
import time
from collections.abc import Callable

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from .cache_context import CacheContext
from .commands import dispatch as dispatch_cmd
from .config import SESSION_DIR
from .llm import TokenCallback, clear_interrupt, request_interrupt, run_conversation
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



def _make_project_context() -> dict | None:
    """生成项目结构感知上下文（不可变前缀成员，跨会话稳定）。"""
    from .tools.project import get_project_context
    context = get_project_context()
    if not context:
        return None
    return {
        "role": "system",
        "content": context,
    }

logger = logging.getLogger(__name__)


def _make_env_message() -> dict:
    """生成环境上下文系统消息。

    重要：不包含任何动态内容（时间戳、cwd等），确保字节级一致
    → DeepSeek 前缀缓存命中率最大化。
    """
    return {
        "role": "system",
        "content": (
            "当前环境：\n"
            f"- 操作系统: {platform.system()} {platform.release()}\n"
            "\n以上为运行环境信息，不需要在回复中复述或罗列。\n\n"
            "你拥有长期记忆，需要时用 recall 搜索相关记忆。"
        ),
        # 注意：不设 _volatile，此消息属于不可变 prefix
    }


def _make_memory_context() -> dict | None:
    """读取记忆索引，生成简洁的记忆上下文（缓存版，不反复读文件）。"""
    from .tools.memory import get_context
    ctx_str = get_context()
    if not ctx_str:
        return None
    return {"role": "system", "content": ctx_str, "_volatile": True}


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


TOOL_LABELS: dict[str, str] = {
    "search_web":    "搜索",
    "read_page":     "阅读网页",
    "read_file":     "读取文件",
    "read_file_lines": "读取文件（带行号）",
    "write_file":    "写入文件",
    "edit_file_lines": "行级编辑",
    "undo_edit":     "撤销编辑",
    "list_files":    "浏览目录",
    "delete_file":   "删除文件",
    "count_lines":   "统计行数",
    "run_python":    "执行代码",
    "run_command":   "执行命令",
    "grep_code":     "搜索代码",
    "think":         "思考",
    "remember":      "记住",
    "recall":        "回忆",
    "forget":        "忘记",
    "install_plugin": "安装插件",
    "list_plugins":   "列出插件",
    "discover":       "能力扫描",
    "plugin_spec":    "插件规范",
    # Git 工具
    "git_status":    "Git 状态",
    "git_diff":      "Git 差异",
    "git_log":       "Git 日志",
    "git_commit":    "Git 提交",
    "git_push":      "Git 推送",
    "git_pr":        "Git PR",
    "git_branch":    "Git 分支",
    "scan_project":  "扫描项目",
    "check_project": "规范检查",
    "generate_agents_md": "生成规范",
    "docs_sync_check": "文档同步检查",
}


def _on_tool(name: str, args: dict) -> None:
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    print(f"  [{label}] {detail}")


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
                messages, summary = load_session(session_id)
                ctx = CacheContext(log_msgs=messages)
                if summary:
                    ctx.log.append({"role": "system", "content": f"之前对话的摘要：{summary}", "_volatile": True})
                print(f"已加载会话 [{session_id}]，共 {len(ctx)} 条消息")
                _print_recent(ctx.all)
                print()
        except ValueError:
            pass

    # 构建 CacheContext：不可变 prefix + 追加 log
    if ctx is None:
        ctx = CacheContext()
    prefix_msgs = []
    prefix_msgs.append(_make_env_message())
    proj_ctx = _make_project_context()
    if proj_ctx:
        prefix_msgs.append(proj_ctx)
    ctx.rebuild_prefix(prefix_msgs)
    from .safety import get_mode_summary
    ctx.log.append({"role": "system", "content": get_mode_summary(), "_volatile": True})
    # ── 失败反思 ──
    try:
        from .tools.reflect import get_summary as _reflect_summary
        ref = _reflect_summary()
        if ref:
            ctx.log.append({"role": "system", "content": ref, "_volatile": True})
    except Exception:
        pass

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
                    loaded_msgs, summary = load_session(r.load)
                    ctx.log.extend(loaded_msgs)
                    if summary:
                        ctx.log.append({"role": "system", "content": f"之前对话的摘要：{summary}", "_volatile": True})
                    session_id = r.load
                    print(f"已加载会话 [{r.load}]，共 {len(ctx)} 条消息")
                    _print_recent(ctx.all)
                if r.clear:
                    ctx.clear_log()
                    # 重建 prefix（环境上下文 + 项目感知）
                    prefix = []
                    try:
                        from .tools.rag import get_index_status
                        rag_info = get_index_status()
                        if "未建立" not in rag_info:
                            prefix.append({
                                "role": "system",
                                "content": "📚 RAG 代码索引已就绪，可用 rag_query 进行语义搜索。",
                                "_volatile": True,
                            })
                    except Exception:
                        pass
                    if r.save:   # /new: 同时重置 session
                        session_id = None
                    prefix.append(_make_env_message())
                    proj_ctx = _make_project_context()
                    if proj_ctx:
                        prefix.append(proj_ctx)
                    ctx.rebuild_prefix(prefix)
                    from .safety import get_mode_summary
                    ctx.log.append({"role": "system", "content": get_mode_summary(), "_volatile": True})
                    # ── 失败反思 ──
                    try:
                        from .tools.reflect import get_summary as _reflect_summary
                        ref = _reflect_summary()
                        if ref:
                            ctx.log.append({"role": "system", "content": ref, "_volatile": True})
                    except Exception:
                        pass
                if r.exit:
                    break
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
                        # ── 主动建议 + 修复闭环 ──
                        try:
                            from .suggest import check as _suggest_check
                            tip, repair = _suggest_check()
                            if tip:
                                print(f"\n💡 {tip}")
                                ans = input("  要修吗？(Y/n) ").strip().lower()
                                if ans != "n":
                                    on_token, has_output = _make_display()
                                    _start_esc_listener()
                                    try:
                                        run_conversation(
                                            ctx, repair, on_token, _on_tool,
                                            on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                                            session_id=session_id,
                                        )
                                    finally:
                                        _stop_esc_listener()
                                    session_id = sid[0]
                                    if has_output():
                                        print()
                        except Exception:
                            pass
                continue

            try:
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
                # ── 主动建议 + 修复闭环 ──
                try:
                    from .suggest import check as _suggest_check
                    tip, repair = _suggest_check()
                    if tip:
                        print(f"\n💡 {tip}")
                        ans = input("  要修吗？(Y/n) ").strip().lower()
                        if ans != "n":
                            on_token, has_output = _make_display()
                            _start_esc_listener()
                            try:
                                run_conversation(
                                    ctx, repair, on_token, _on_tool,
                                    on_progress=lambda sid=sid: sid.__setitem__(0, save_session(ctx.all, sid[0])),
                                    session_id=session_id,
                                )
                            finally:
                                _stop_esc_listener()
                            session_id = sid[0]
                            if has_output():
                                print()
                except Exception:
                    pass
            except KeyboardInterrupt:
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
            except Exception as e:
                logger.error("对话出错: %s", e)
                print(f"\n  请求失败: {e}\n")
    finally:
        # 只有存在至少一条 assistant 回复时才保存（避免网络错误等空会话落盘）
        has_response = any(m["role"] == "assistant" for m in ctx.all)
        if has_response:
            session_id = save_session(ctx.all, session_id)
            print(f"会话已保存: [{session_id}]")
        print("退出")


if __name__ == "__main__":
    main()
