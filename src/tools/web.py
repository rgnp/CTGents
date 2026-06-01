"""Web 工具：search_web + read_page，带 TTL 缓存和超时控制。"""

import json
import time
import urllib.request
import urllib.error
from typing import Any

import trafilatura

from ..config import get_tavily_client

tavily = get_tavily_client()

# ── TTL 缓存 ──
_CACHE_TTL_SEARCH = 300    # 搜索结果 5 分钟有效
_CACHE_TTL_PAGE = 600      # 网页成功内容 10 分钟有效
_CACHE_TTL_ERROR = 30      # 网络/超时等临时错误 30 秒有效（快速重试）
_CACHE_TTL_NOT_FOUND = 600 # 404/410 长期缓存
_CACHE_MAX_SIZE = 200      # 最大条目数
_PAGE_MAX_CHARS = 8000     # 页面内容最大字符数（超出截断）
_FETCH_TIMEOUT = 15        # HTTP 请求超时秒数

_web_cache: dict[str, dict[str, Any]] = {}
_cache_hits: int = 0
_cache_misses: int = 0


def _cache_key(name: str, args: dict) -> str:
    """生成缓存键：工具名 + 参数排序后 JSON。"""
    return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _cache_get(name: str, args: dict, ttl: int) -> str | None:
    """从缓存读取，过期返回 None。"""
    global _cache_hits
    key = _cache_key(name, args)
    entry = _web_cache.get(key)
    if entry is not None and (time.time() - entry["t"]) < ttl:
        _cache_hits += 1
        return entry["v"]
    return None


def _cache_set(name: str, args: dict, value: str, ttl_override: int | None = None) -> None:
    """写入缓存。如果 ttl_override 非 None，存为不同的过期时间。"""
    global _cache_misses
    _cache_misses += 1
    key = _cache_key(name, args)
    if len(_web_cache) >= _CACHE_MAX_SIZE:
        # 淘汰最旧 20%
        items = sorted(_web_cache.items(), key=lambda x: x[1]["t"])
        for k, _v in items[: max(1, len(items) // 5)]:
            del _web_cache[k]
    _web_cache[key] = {"v": value, "t": time.time()}
    if ttl_override is not None:
        _web_cache[key]["ttl"] = ttl_override


def _cache_get_any(name: str, args: dict) -> str | None:
    """从缓存读取，使用条目自带的 TTL（如果有）或默认 TTL。"""
    global _cache_hits
    key = _cache_key(name, args)
    entry = _web_cache.get(key)
    if entry is None:
        return None
    ttl = entry.get("ttl", _CACHE_TTL_PAGE)
    if (time.time() - entry["t"]) < ttl:
        _cache_hits += 1
        return entry["v"]
    return None


def get_web_cache_stats() -> dict:
    """返回缓存统计信息。"""
    return {
        "entries": len(_web_cache),
        "hits": _cache_hits,
        "misses": _cache_misses,
        "max_size": _CACHE_MAX_SIZE,
    }


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
    """将 Tavily 搜索结果格式化为可读文本。"""
    results = response.get("results", [])
    if not results:
        return f"搜索「{query}」未返回结果。"

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
    """搜索互联网并返回格式化结果（带缓存）。"""
    cached = _cache_get("search_web", {"query": query}, _CACHE_TTL_SEARCH)
    if cached is not None:
        return cached

    result = tavily.search(query, max_results=5)
    formatted = format_search_results(result, query)
    _cache_set("search_web", {"query": query}, formatted)
    return formatted


def _fetch_url_with_timeout(url: str) -> tuple[str | None, int | None]:
    """用 urllib 下载网页，带超时控制。

    Returns:
        (html, status_code)
        html=None 表示失败
        status_code 为 HTTP 状态码，网络错误时为 None
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            # 跳过明显的非 HTML 二进制（但允许空 Content-Type）
            skip_ct = {
                "application/pdf", "application/zip", "application/octet-stream",
                "application/gzip", "application/x-tar",
            }
            if content_type and (
                any(content_type.startswith(t) for t in skip_ct)
                or content_type.startswith("image/")
                or content_type.startswith("video/")
                or content_type.startswith("audio/")
            ):
                return None, status
            raw = resp.read()
            for encoding in ("utf-8", "gbk", "latin-1"):
                try:
                    return raw.decode(encoding), status
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace"), status
    except urllib.error.HTTPError as e:
        return None, e.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None
    except Exception:
        return None, None


def _error_message(url: str, status: int | None) -> str:
    """根据状态码生成用户友好的错误消息。"""
    if status is None:
        return f"无法访问 {url}，可能是网络超时或 DNS 解析失败。"
    if status == 404:
        return f"{url} 页面不存在（404）。"
    if status == 410:
        return f"{url} 页面已被永久移除（410）。"
    if status == 403:
        return f"{url} 拒绝访问（403）。"
    if status == 429:
        return f"{url} 请求过于频繁，被限流（429）。"
    if status and status >= 500:
        return f"{url} 服务器错误（{status}）。"
    if status:
        return f"无法访问 {url}，HTTP {status}。"
    return f"无法访问 {url}，可能是网站限制或网络问题。"


def read_page(url: str) -> str:
    """抓取并提取网页正文（带缓存 + 超时 + 截断）。

    缓存策略：
    - 成功内容：10 分钟
    - 404/410：10 分钟（永久性错误，不重试）
    - 网络/超时：30 秒（临时故障，快速重试窗口）
    - 其他 HTTP 错误：30 秒
    """
    cached = _cache_get_any("read_page", {"url": url})
    if cached is not None:
        return cached

    # 带超时下载
    html, status = _fetch_url_with_timeout(url)

    # ── 下载失败：根据状态码决定缓存策略 ──
    if html is None:
        result = _error_message(url, status)
        # 404/410 长缓存；其他错误短缓存
        ttl = _CACHE_TTL_NOT_FOUND if status in (404, 410) else _CACHE_TTL_ERROR
        _cache_set("read_page", {"url": url}, result, ttl_override=ttl)
        return result

    # 提取正文
    text = trafilatura.extract(html, include_links=False, include_images=False)
    if not text:
        result = f"{url} 未能提取到有效正文内容。"
        _cache_set("read_page", {"url": url}, result)
        return result

    # 截断过长内容
    if len(text) > _PAGE_MAX_CHARS:
        text = text[:_PAGE_MAX_CHARS] + (
            f"\n\n[已压缩：原始结果 {len(text)} 字符，"
            f"仅显示前 {_PAGE_MAX_CHARS} 字符。"
            "如需完整内容，可重新搜索或阅读页面]"
        )

    _cache_set("read_page", {"url": url}, text)
    return text


def execute(name: str, args: dict) -> str | None:
    if name == "search_web":
        return search_web(args["query"])
    if name == "read_page":
        return read_page(args["url"])
    return None
