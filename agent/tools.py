import json

from config import get_tavily_client
from openai.types.chat import ChatCompletionMessageToolCall

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
    }
]


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


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行单个工具调用，返回格式化后的结果文本。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "search_web":
        result = tavily.search(args["query"], max_results=5)
        return _format_search_results(result, args["query"])

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
