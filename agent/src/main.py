import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import SESSION_DIR
from .llm import run_conversation, TokenCallback, ToolCallback
from .session import list_sessions, load_session, save_session

logger = logging.getLogger(__name__)


# ── 指令系统 ──

@dataclass
class CmdResult:
    message: str = ""
    exit: bool = False
    save: bool = False
    clear: bool = False


CmdHandler = Callable[["CmdResult", list[dict], list[str]], None]

COMMANDS: dict[str, CmdHandler] = {}


def _register(*names: str):
    def deco(fn: CmdHandler):
        for name in names:
            COMMANDS[name] = fn
        return fn
    return deco


@_register("/exit", "/quit", "/q")
def _cmd_exit(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """退出程序"""
    r.exit = True


@_register("/help", "/h", "/?")
def _cmd_help(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """显示指令列表"""
    lines = ["可用指令："]
    for name in sorted(COMMANDS):
        doc = COMMANDS[name].__doc__ or ""
        lines.append(f"  {name:<12} {doc}")
    r.message = "\n".join(lines)


@_register("/clear", "/c")
def _cmd_clear(r: CmdResult, msgs: list[dict], _args: list[str]) -> None:
    """清除对话上下文"""
    msgs.clear()
    r.message = "上下文已清除"


@_register("/save")
def _cmd_save(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """强制保存当前会话"""
    r.save = True


@_register("/sessions", "/ls")
def _cmd_sessions(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """列出历史会话"""
    sessions = list_sessions()
    if not sessions:
        r.message = "没有历史会话"
        return
    lines = ["历史会话："]
    for i, sid in enumerate(sessions, 1):
        summary_path = os.path.join(SESSION_DIR, sid, "summary.txt")
        preview = ""
        try:
            if os.path.exists(summary_path):
                with open(summary_path, "r", encoding="utf-8") as f:
                    preview = f.read()[:60].replace("\n", " ")
        except Exception:
            pass
        lines.append(f"  [{i}] {sid}  {preview}")
    r.message = "\n".join(lines)


@_register("/new")
def _cmd_new(r: CmdResult, _msgs: list[dict], _args: list[str]) -> None:
    """新建会话（自动保存当前）"""
    r.save = True
    r.clear = True
    r.message = ""


def _dispatch(user_input: str, messages: list[dict]) -> CmdResult:
    r = CmdResult()
    parts = user_input.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handler = COMMANDS.get(cmd)
    if handler:
        handler(r, messages, args)
    return r


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
        content = m["content"]
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

    if not session_id:
        print("Agent 已启动，输入 /help 查看指令列表\n")
    else:
        print("Agent 已启动，输入 /help 查看指令列表\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue

            # 指令分发
            if user_input.startswith("/"):
                cmd_r = _dispatch(user_input, messages)
                if cmd_r.message:
                    print(cmd_r.message)
                if cmd_r.clear:
                    messages.clear()
                if cmd_r.save:
                    sid = save_session(messages, session_id)
                    print(f"会话已保存: [{sid}]")
                if cmd_r.exit:
                    break
                continue

            try:
                on_token, has_output = _make_display()
                reply = run_conversation(messages, user_input, on_token, _on_tool)
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
