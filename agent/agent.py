import logging
import time

from openai import APIError, APITimeoutError, RateLimitError, APIConnectionError, InternalServerError

from config import get_llm_client, DEEPSEEK_MODEL, MAX_TOOL_ROUNDS, MAX_RETRIES, RETRY_BASE_DELAY
from session import list_sessions, load_session, save_session
from tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)
client = get_llm_client()

RETRYABLE = (APITimeoutError, RateLimitError, APIConnectionError, InternalServerError)


def call_llm(messages: list[dict]) -> str:
    """调用 LLM，可重试的错误自动指数退避重试。"""
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                tools=TOOLS,
            )
            return response
        except RETRYABLE as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"  网络波动，正在重试 ({attempt}/{MAX_RETRIES})...")
                time.sleep(delay)
        except Exception:
            raise

    raise last_error  # type: ignore[misc]


def run_conversation(messages: list[dict], user_input: str) -> str:
    """处理一轮对话：副本上操作，跑通后一次性提交到 messages。"""
    copy: list[dict] = list(messages)
    copy.append({"role": "user", "content": user_input})

    for _ in range(MAX_TOOL_ROUNDS):
        response = call_llm(copy)
        msg = response.choices[0].message

        if msg.tool_calls:
            copy.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                result = execute_tool(tc)
                copy.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            copy.append({"role": "assistant", "content": msg.content})
            messages[:] = copy
            return msg.content

    messages[:] = copy
    return "已达到最大搜索轮数，请简化问题重试。"


def _print_sessions(sessions: list[str]) -> None:
    print("历史会话：")
    for i, sid in enumerate(sessions, 1):
        summary_path = f"sessions/{sid}/summary.txt"
        preview = ""
        try:
            import os
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
                print(f"Agent: {reply}\n")
            except Exception as e:
                logger.error("对话出错: %s", e)
                print(f"  请求失败: {e}\n")
    finally:
        if messages:
            session_id = save_session(messages, session_id)
            print(f"会话已保存: [{session_id}]")
        print("退出")


if __name__ == "__main__":
    main()
