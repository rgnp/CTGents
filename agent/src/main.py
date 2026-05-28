import logging
import os
import sys
from collections.abc import Callable

from .commands import CmdResult, dispatch as dispatch_cmd
from .config import SESSION_DIR
from .llm import run_conversation, TokenCallback, ToolCallback
from .session import list_sessions, load_session, save_session

logger = logging.getLogger(__name__)


# ── UI 辅助 ──

def _print_sessions(sessions: list[str]) -> None:
    print("历史会话：")
    for i, sid in enumerate(sessions, 1):
        summary_path = os.path.join(SESSION_DIR, sid, "summary.txt")
        preview = ""
        try:
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
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
    "search_web":   "搜索",
    "read_page":    "阅读网页",
    "read_file":    "读取文件",
    "write_file":   "写入文件",
    "list_files":   "浏览目录",
    "delete_file":  "删除文件",
    "run_python":   "执行代码",
    "grep_code":    "搜索代码",
    "think":        "思考",
    "install_plugin": "安装插件",
    "list_plugins":  "列出插件",
    "skill_discover": "发现技能",
}


def _on_tool(name: str, args: dict) -> None:
    label = TOOL_LABELS.get(name, name)
    detail = " ".join(f"{k}={v}" for k, v in args.items())
    if len(detail) > 80:
        detail = detail[:77] + "..."
    print(f"  [{label}] {detail}")


# ── 主入口 ──

def main() -> None:
    sessions = list_sessions()
    session_id: str | None = None
    messages: list[dict] = []

    if sessions:
        _print_sessions(sessions)
        print()
        choice = input("输入编号加载会话，或直接回车新建: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session_id = sessions[idx]
                messages, summary = load_session(session_id)
                if summary:
                    messages.insert(0, {"role": "system", "content": f"之前对话的摘要：{summary}"})
                print(f"已加载会话 [{session_id}]，共 {len(messages)} 条消息")
                _print_recent(messages)
                print()
        except ValueError:
            pass

    print("Agent 已启动，输入 /help 查看指令列表\n")

    _use_rich_input = sys.stdin.isatty()
    if _use_rich_input:
        from prompt_toolkit import prompt
        from prompt_toolkit.key_binding import KeyBindings

        kb = KeyBindings()

        @kb.add("escape")
        def _(event):
            """Esc 撤回最后一条对话。"""
            while messages and messages[-1]["role"] != "user":
                messages.pop()
            if messages and messages[-1]["role"] == "user":
                messages.pop()
                save_session(messages, session_id)
                print("\r已撤回 ── 输入新问题或回车重新发送", end="")
            event.app.exit(result="")

    try:
        while True:
            try:
                if _use_rich_input:
                    user_input = prompt("You: ", key_bindings=kb).strip()
                else:
                    user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                r = dispatch_cmd(user_input, messages, session_id)
                if r.message:
                    print(r.message)
                if r.save:
                    session_id = save_session(messages, session_id)
                    print(f"会话已保存: [{session_id}]")
                if r.load:
                    messages, summary = load_session(r.load)
                    if summary:
                        messages.insert(0, {"role": "system", "content": f"之前对话的摘要：{summary}"})
                    session_id = r.load
                    print(f"已加载会话 [{r.load}]，共 {len(messages)} 条消息")
                    _print_recent(messages)
                if r.clear:
                    messages.clear()
                    if r.save:   # /new: 同时重置 session
                        session_id = None
                if r.exit:
                    break
                if r.retry:
                    last_user = next(
                        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
                    )
                    if last_user:
                        on_token, has_output = _make_display()
                        reply = run_conversation(
                            messages, last_user, on_token, _on_tool,
                            on_progress=lambda: save_session(messages, session_id),
                        )
                        if has_output():
                            print()
                continue

            try:
                on_token, has_output = _make_display()
                reply = run_conversation(
                    messages, user_input, on_token, _on_tool,
                    on_progress=lambda: save_session(messages, session_id),
                )
                if has_output():
                    print()
            except KeyboardInterrupt:
                print("\n[中断]")
                try:
                    guide = input("指导: ").strip()
                except (EOFError, KeyboardInterrupt):
                    guide = ""
                if guide:
                    on_token, has_output = _make_display()
                    reply = run_conversation(
                        messages, guide, on_token, _on_tool,
                        on_progress=lambda: save_session(messages, session_id),
                    )
                    if has_output():
                        print()
            except Exception as e:
                logger.error("对话出错: %s", e)
                print(f"\n  请求失败: {e}\n")
    finally:
        if messages:
            session_id = save_session(messages, session_id)
            print(f"会话已保存: [{session_id}]")
        print("退出")


if __name__ == "__main__":
    main()
