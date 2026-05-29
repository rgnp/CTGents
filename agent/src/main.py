import logging
import os
import platform
import sys
from collections.abc import Callable
from datetime import datetime

from .commands import CmdResult, dispatch as dispatch_cmd
from .config import SESSION_DIR
from .llm import run_conversation, TokenCallback, ToolCallback
from .session import list_sessions, load_session, save_session

logger = logging.getLogger(__name__)


def _make_env_message() -> dict:
    """生成环境上下文系统消息，标记 _volatile 以在保存时自动过滤。"""
    now = datetime.now()
    return {
        "role": "system",
        "content": (
            f"当前环境：\n"
            f"- 工作目录: {os.getcwd()}\n"
            f"- 当前时间: {now.strftime('%Y年%m月%d日 %H:%M:%S')}（星期{['一','二','三','四','五','六','日'][now.weekday()]}）\n"
            f"- 操作系统: {platform.system()} {platform.release()}\n"
            f"\n以上为运行环境信息，不需要在回复中复述或罗列。"
        ),
        "_volatile": True,
    }


def _make_memory_context() -> dict | None:
    """读取记忆文件的 frontmatter，生成简洁的记忆索引。"""
    from .config import MEMORY_DIR
    import re
    mem_dir = os.path.join(MEMORY_DIR, "MEMORY.md")
    if not os.path.exists(mem_dir):
        return None

    # 从各 .md 文件 frontmatter 提取 name + description 首句
    entries: list[str] = []
    d = os.path.dirname(mem_dir)
    for f in sorted(os.listdir(d)):
        if f == "MEMORY.md" or not f.endswith(".md"):
            continue
        try:
            text = open(os.path.join(d, f), encoding="utf-8").read()
            name, desc = f[:-3], ""
            if text.startswith("---"):
                fm = text[3:text.find("---", 3)]
                for line in fm.split("\n"):
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().rstrip(".")
            # 只取 description 第一句（到第一个句号或 30 字）
            short = desc.split("。")[0].split("，")[0][:30] if desc else ""
            entries.append((name, short))
        except Exception:
            continue

    if not entries:
        return None

    lines = ["你拥有以下记忆（需要时用 recall 搜索详情，不要在回复中逐字复述）："]
    for name, short in entries:
        lines.append(f"  {name}: {short}")
    return {"role": "system", "content": "\n".join(lines), "_volatile": True}

def _make_project_context() -> dict | None:
    """生成项目结构感知上下文。"""
    from .tools.project import scan_project
    try:
        content = scan_project()
        if not content:
            return None
        return {"role": "system", "content": content, "_volatile": True}
    except Exception:
        return None





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
    "read_file_lines": "读取文件（带行号）",
    "write_file":   "写入文件",
    "edit_file_lines": "行级编辑",
    "undo_edit":    "撤销编辑",
    "list_files":   "浏览目录",
    "delete_file":  "删除文件",
    "run_python":   "执行代码",
    "run_command":  "执行命令",
    "grep_code":    "搜索代码",
    "think":        "思考",
    "remember":     "记住",
    "recall":       "回忆",
    "forget":       "忘记",
    "install_plugin": "安装插件",
    "list_plugins":  "列出插件",
    "discover":      "能力扫描",
    "skill_list":    "Skill列表",
    "skill_show":    "Skill查看",
    "skill_load":    "Skill加载",
    "skill_create":  "Skill创建",
    "git_status":   "Git状态",
    "git_diff":     "Git差异",
    "git_add":      "Git暂存",
    "git_commit":   "Git提交",
    "git_push":     "Git推送",
    "git_log":      "Git日志",
    "git_branch":   "Git分支",
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

    # 注入环境上下文（每次启动刷新，不持久化到磁盘）
    messages.insert(0, _make_env_message())

    # 注入已有记忆索引
    mem_ctx = _make_memory_context()
    # 注入项目结构感知
    proj_ctx = _make_project_context()
    if proj_ctx:
        messages.insert(2, proj_ctx)
    if mem_ctx:
        messages.insert(1, mem_ctx)

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
                # ── 热加载：拦截 /reload，不经过旧 dispatch ──
                if user_input.lower().startswith("/reload"):
                    ok, msg = _reload_dispatch()
                    print(msg)
                    # 注入一条系统消息，告知 LLM 工具已更新
                    messages.append({
                        "role": "system",
                        "content": "⚠️ 系统已热加载，工具列表已更新。执行 discover 查看最新可用工具。"
                    })
                    continue

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
                    # 重新注入环境上下文、记忆索引、项目感知
                    messages.insert(0, _make_env_message())
                    mem_ctx = _make_memory_context()
                    if mem_ctx:
                        messages.insert(1, mem_ctx)
                    proj_ctx = _make_project_context()
                    if proj_ctx:
                        messages.insert(2, proj_ctx)
                if r.exit:
                    break
                if r.retry:
                    last_user = next(
                        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
                    )
                    if last_user:
                        on_token, has_output = _make_display()
                        sid = [session_id]
                        reply = run_conversation(
                            messages, last_user, on_token, _on_tool,
                            on_progress=lambda: sid.__setitem__(0, save_session(messages, sid[0])),
                        )
                        session_id = sid[0]
                        if has_output():
                            print()
                continue

            try:
                on_token, has_output = _make_display()
                sid = [session_id]
                reply = run_conversation(
                    messages, user_input, on_token, _on_tool,
                    on_progress=lambda: sid.__setitem__(0, save_session(messages, sid[0])),
                )
                session_id = sid[0]
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
                    sid = [session_id]
                    reply = run_conversation(
                        messages, guide, on_token, _on_tool,
                        on_progress=lambda: sid.__setitem__(0, save_session(messages, sid[0])),
                    )
                    session_id = sid[0]
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
