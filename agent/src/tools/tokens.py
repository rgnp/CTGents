from ..config import MAX_CONTEXT_TOKENS, TOKEN_PER_CHAR, TOOL_RESULT_BUDGET


def estimate_tokens(text: str) -> int:
    """估算文本 token 数。采用保守系数，宁可多估不漏估。"""
    return int(len(text) * TOKEN_PER_CHAR)


def count_messages_tokens(messages: list[dict]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
    return total


def truncate_to_budget(raw_text: str, messages: list[dict]) -> str:
    """根据当前消息列表的剩余 token 预算动态截断文本。
    不设固定上限——剩余空间多就多留，少就少留。
    """
    used = count_messages_tokens(messages)
    remaining = MAX_CONTEXT_TOKENS - used

    if remaining <= 0:
        return "上下文已满，无法添加更多内容。请精简之前的对话。"

    budget = int(remaining * TOOL_RESULT_BUDGET)

    if estimate_tokens(raw_text) <= budget:
        return raw_text

    max_chars = int(budget / TOKEN_PER_CHAR)
    return raw_text[:max_chars] + (
        f"\n\n...（token 预算截断：已用 {used} / {MAX_CONTEXT_TOKENS}，"
        f"本次工具结果限 {budget} tokens）"
    )
