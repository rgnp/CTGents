import json
from pathlib import Path

import trafilatura
from openai.types.chat import ChatCompletionMessageToolCall

from .config import get_tavily_client, MAX_CONTEXT_TOKENS, TOOL_RESULT_BUDGET, TOKEN_PER_CHAR

tavily = get_tavily_client()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "搜索互联网获取信息。"
                "搜索结果是唯一信息来源——结果不相关或为空时必须如实告知未找到，不得编造。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，用中英文均可",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "打开指定 URL 读取网页全文。搜索结果只有摘要，需要详细内容时用这个工具深入阅读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要阅读的网页 URL",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件内容。可以读取项目中的代码、文档、配置等文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持相对路径或绝对路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆写本地文件。用于保存报告、代码、笔记等。写入前会告知用户即将写入的路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持相对路径或绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


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
    不设固定上限——剩余空间多就多留，少就少留。"""
    used = count_messages_tokens(messages)
    remaining = MAX_CONTEXT_TOKENS - used

    if remaining <= 0:
        return "上下文已满，无法添加更多内容。请精简之前的对话。"

    budget = int(remaining * TOOL_RESULT_BUDGET)

    if estimate_tokens(raw_text) <= budget:
        return raw_text

    # 从预算反推最大字符数
    max_chars = int(budget / TOKEN_PER_CHAR)
    return raw_text[:max_chars] + (
        f"\n\n...（token 预算截断：已用 {used} / {MAX_CONTEXT_TOKENS}，"
        f"本次工具结果限 {budget} tokens）"
    )


def _format_search_results(response: dict, query: str) -> str:
    """将 Tavily 原始响应格式化为 LLM 可读的文本，仅保留 title/url/content。"""
    results = response.get("results", [])

    if not results:
        return f'搜索「{query}」未找到相关结果。'

    lines = [f'搜索「{query}」返回 {len(results)} 条结果：\n']
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')}")
        lines.append(f"   {r.get('content', '')}")
        lines.append(f"   {r.get('url', '')}")
        lines.append("")
    return "\n".join(lines)


def _read_page(url: str) -> str:
    """抓取并提取网页正文，返回原始结果（截断由调用方处理）。"""
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        return f"无法访问 {url}，可能是网站限制或网络问题。"
    text = trafilatura.extract(downloaded, include_links=False, include_images=False)
    if not text:
        return f"{url} 未能提取到有效正文内容。"
    return text


def _read_file(path: str) -> str:
    """读取本地文件内容，返回原始结果（截断由调用方处理）。"""
    filepath = Path(path).expanduser().resolve()
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    try:
        return filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"


def _write_file(path: str, content: str) -> str:
    """写入文件。"""
    filepath = Path(path).expanduser().resolve()
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        return f"已写入: {filepath}（{len(content)} 字符）"
    except OSError as e:
        return f"写入失败: {e}"


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行单个工具调用，返回原始结果（截断由调用方处理）。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "search_web":
        result = tavily.search(args["query"], max_results=5)
        return _format_search_results(result, args["query"])

    if name == "read_page":
        return _read_page(args["url"])

    if name == "read_file":
        return _read_file(args["path"])

    if name == "write_file":
        return _write_file(args["path"], args["content"])

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
