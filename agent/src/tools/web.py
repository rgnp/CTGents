import trafilatura

from ..config import get_tavily_client

tavily = get_tavily_client()

TOOLS_WEB = [
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
]


def format_search_results(response: dict, query: str) -> str:
    """格式化搜索结果：仅标题 + 简短片段 + 域名 + 链接，供 agent 筛选后深入阅读。"""
    results = response.get("results", [])

    if not results:
        return f"搜索「{query}」未找到相关结果。"

    lines = [f"搜索「{query}」返回 {len(results)} 条结果。深入阅读请用 read_page：\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("content", "")[:100]
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
        except Exception:
            domain = ""

        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append(f"   [{domain}] {url}")
        lines.append("")
    return "\n".join(lines)


def search_web(query: str) -> str:
    """搜索互联网并返回格式化结果。"""
    result = tavily.search(query, max_results=5)
    return format_search_results(result, query)


def read_page(url: str) -> str:
    """抓取并提取网页正文，返回原始结果（截断由调用方处理）。"""
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        return f"无法访问 {url}，可能是网站限制或网络问题。"
    text = trafilatura.extract(downloaded, include_links=False, include_images=False)
    if not text:
        return f"{url} 未能提取到有效正文内容。"
    return text


def execute(name: str, args: dict) -> str | None:
    if name == "search_web":
        return search_web(args["query"])
    if name == "read_page":
        return read_page(args["url"])
    return None
