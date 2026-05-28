import json
import logging
import time
from collections.abc import Callable
from types import SimpleNamespace

from openai import APITimeoutError, RateLimitError, APIConnectionError, InternalServerError

from .config import get_llm_client, DEEPSEEK_MODEL, MAX_CONTEXT_TOKENS, TOOL_LOOP_THRESHOLD, MAX_RETRIES, RETRY_BASE_DELAY
from .tools import TOOLS, execute_tool, truncate_to_budget
from .tools.tokens import count_messages_tokens

logger = logging.getLogger(__name__)
client = get_llm_client()

RETRYABLE = (APITimeoutError, RateLimitError, APIConnectionError, InternalServerError)
TokenCallback = Callable[[str], None]
ToolCallback = Callable[[str, dict], None]


def _stream_llm(
    messages: list[dict], on_token: TokenCallback
) -> tuple[str | None, list[dict] | None]:
    """流式调用 LLM，通过 on_token 回调输出 token。
    返回 (content, tool_calls_dict_list)。"""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                return _do_stream(messages, on_token)
            else:
                return _do_non_stream(messages, on_token)
        except RETRYABLE as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("网络波动，正在重试 (%d/%d)...", attempt, MAX_RETRIES)
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise

    raise RuntimeError("unreachable")


def _do_stream(
    messages: list[dict], on_token: TokenCallback
) -> tuple[str | None, list[dict] | None]:
    content_parts: list[str] = []
    tool_calls: list[dict] = []

    stream = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        tools=TOOLS,
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta

        if delta.content:
            on_token(delta.content)
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

    content = "".join(content_parts) if content_parts else None
    return content, (tool_calls if tool_calls else None)


def _do_non_stream(
    messages: list[dict], on_token: TokenCallback
) -> tuple[str | None, list[dict] | None]:
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
        on_token(msg.content)

    return msg.content, tool_calls


def run_conversation(
    messages: list[dict],
    user_input: str,
    on_token: TokenCallback,
    on_tool: ToolCallback,
    on_progress: Callable[[], None] | None = None,
) -> str:
    """处理一轮对话：每轮工具调用后增量提交到 messages 并回调 on_progress。"""
    copy: list[dict] = list(messages)
    copy.append({"role": "user", "content": user_input})
    messages[:] = copy  # 用户消息立即提交

    while True:
        used = count_messages_tokens(copy)
        limit = int(MAX_CONTEXT_TOKENS * TOOL_LOOP_THRESHOLD)
        if used >= limit:
            return (
                f"上下文用量已达上限（{used}/{MAX_CONTEXT_TOKENS} tokens）。"
                "请开启新会话或精简问题。"
            )

        content, tool_calls = _stream_llm(copy, on_token)

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
                args = json.loads(tc_data["function"]["arguments"])
                on_tool(tc_data["function"]["name"], args)
                result = execute_tool(tc)
                result = truncate_to_budget(result, copy)
                copy.append({
                    "role": "tool",
                    "tool_call_id": tc_data["id"],
                    "content": result,
                })
            messages[:] = copy  # 每轮工具调用后增量提交
            if on_progress:
                on_progress()
        else:
            copy.append({"role": "assistant", "content": content})
            messages[:] = copy
            if on_progress:
                on_progress()
            return content or ""
