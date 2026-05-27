import logging
import os
import time
from types import SimpleNamespace

from openai import APITimeoutError, RateLimitError, APIConnectionError, InternalServerError

from config import get_llm_client, DEEPSEEK_MODEL, MAX_TOOL_ROUNDS, MAX_RETRIES, RETRY_BASE_DELAY
from session import list_sessions, load_session, save_session
from tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)
client = get_llm_client()

RETRYABLE = (APITimeoutError, RateLimitError, APIConnectionError, InternalServerError)


def _stream_llm(messages: list[dict]) -> tuple[str | None, list[dict] | None]:
    """流式调用 LLM，实时打印 token。首试流式，失败退化为非流式重试。
    返回 (content, tool_calls_dict_list)。"""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                return _do_stream(messages)
            else:
                return _do_non_stream(messages)
        except RETRYABLE as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"\n  网络波动，正在重试 ({attempt}/{MAX_RETRIES})...")
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise

    raise RuntimeError("unreachable")


def _do_stream(messages: list[dict]) -> tuple[str | None, list[dict] | None]:
    content_parts: list[str] = []
    tool_calls: list[dict] = []
    prefix_printed = False

    stream = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        tools=TOOLS,
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta

        if delta.content:
            if not prefix_printed:
                print("Agent: ", end="", flush=True)
                prefix_printed = True
            print(delta.content, end="", flush=True)
            content_parts.append(delta.content)

        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                while len(tool_calls) <= idx:
                    tool_calls.append({
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                tc = tool_calls[idx]
                if tc_delta.id:
                    tc["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tc["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        tc["function"]["arguments"] += tc_delta.function.arguments

    if prefix_printed and not tool_calls:
        print()

    content = "".join(content_parts) if content_parts else None
    return content, (tool_calls if tool_calls else None)


def _do_non_stream(messages: list[dict]) -> tuple[str | None, list[dict] | None]:
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        tools=TOOLS,
    )
    msg = response.choices[0].message

    tool_calls: list[dict] | None = None
    if msg.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    if msg.content:
        print(f"Agent: {msg.content}")

    return msg.content, tool_calls


def run_conversation(messages: list[dict], user_input: str) -> str:
    """处理一轮对话：副本上操作，流式输出，跑通后一次性提交到 messages。"""
    copy: list[dict] = list(messages)
    copy.append({"role": "user", "content": user_input})

    for _ in range(MAX_TOOL_ROUNDS):
        content, tool_calls = _stream_llm(copy)

        if tool_calls:
            copy.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            for tc_data in tool_calls:
                tc = SimpleNamespace(
                    function=SimpleNamespace(
                        name=tc_data["function"]["name"],
                        arguments=tc_data["function"]["arguments"],
                    )
                )
                result = execute_tool(tc)
                copy.append({
                    "role": "tool",
                    "tool_call_id": tc_data["id"],
                    "content": result,
                })
        else:
            copy.append({"role": "assistant", "content": content})
            messages[:] = copy
            return content or ""

    messages[:] = copy
    return "已达到最大搜索轮数，请简化问题重试。"


def _print_sessions(sessions: list[str]) -> None:
    from config import SESSION_DIR
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
        print("Agent 已启动（可上网搜索），输入 /exit 退出\n")
    else:
        print("Agent 已启动（可上网搜索），输入 /exit 退出（退出时自动保存）\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                continue
            if user_input == "/exit":
                break

            try:
                reply = run_conversation(messages, user_input)
                if reply:
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
