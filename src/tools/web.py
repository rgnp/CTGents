"""Web 工具：search_web + read_page，带 TTL 缓存和超时控制。"""

import json
import re
import time
import urllib.error
import urllib.request
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
_PAGE_MAX_HTML_BYTES = 300_000  # 下载 HTML 最大字节数（超出截断，避免 trafilatura 解析巨型页面）
_FETCH_TIMEOUT = 8           # HTTP 请求超时秒数（8s 足够，15s 过宽松）
_TAVILY_TIMEOUT = 10         # Tavily API 调用超时秒数

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
        "_meta": {"label": "搜索", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索互联网获取最新信息，结果不相关时必须如实告知。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "_meta": {"label": "阅读网页", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "打开 URL 读取网页全文，搜索结果摘要不完整时用此深入阅读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL",
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


def _tavily_search_with_timeout(query: str) -> dict:
    """带超时的 Tavily 搜索调用。"""
    import threading

    result_container: dict | None = None
    error_container: Exception | None = None

    def _run():
        nonlocal result_container, error_container
        try:
            result_container = tavily.search(query, max_results=5)
        except Exception as e:
            error_container = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_TAVILY_TIMEOUT)

    if t.is_alive():
        raise TimeoutError(f"Tavily 搜索超时（{_TAVILY_TIMEOUT}s）")
    if error_container is not None:
        raise error_container
    if result_container is None:
        raise RuntimeError("Tavily 搜索未返回结果")
    return result_container


def search_web(query: str) -> str:
    """搜索互联网并返回格式化结果（带缓存 + 超时 + quota 自愈）。"""
    cached = _cache_get("search_web", {"query": query}, _CACHE_TTL_SEARCH)
    if cached is not None:
        return cached

    from tavily import UsageLimitExceededError

    try:
        result = _tavily_search_with_timeout(query)
    except UsageLimitExceededError:
        result = _try_self_heal(query)
        if result is None:
            return "Tavily API 配额耗尽，所有 key 均已不可用。"
    except TimeoutError:
        return f"搜索超时（{_TAVILY_TIMEOUT}s），请稍后重试或用更具体的关键词。"
    except Exception as e:
        return f"搜索失败: {type(e).__name__}: {e}"

    formatted = format_search_results(result, query)
    formatted += (
        "\n💡 有价值的发现？用 write_file 保存到 knowledge/search/<主题>.md，"
        "以后 rag_search 可检索。"
    )
    _cache_set("search_web", {"query": query}, formatted)
    return formatted


def _try_self_heal(query: str):
    """自愈：重读 .env → 重建 tavily 客户端 → 用所有 key 重试一次。

    当 Tavily 报 quota exceeded 时调用。如果磁盘 .env 被另一会话加过新 key，
    这里会捡起来——agent 不需要重启或人工 reload。

    返回 search result dict，失败返回 None。
    """
    global tavily
    import os
    from pathlib import Path

    from dotenv import load_dotenv
    from tavily import InvalidAPIKeyError, UsageLimitExceededError

    from ..config import MultiKeyTavilyClient

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path, override=True)
    keys_raw = os.getenv("TAVILY_API_KEYS", os.getenv("TAVILY_API_KEY", ""))
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        return None
    tavily = MultiKeyTavilyClient(keys)
    try:
        return _tavily_search_with_timeout(query)
    except (UsageLimitExceededError, InvalidAPIKeyError, TimeoutError):
        return None


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
            raw = resp.read(_PAGE_MAX_HTML_BYTES)
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


def _rewrite_url(url: str) -> str:
    """将 JS 渲染网站的 URL 重写为可读取的静态版本。"""
    m = re.match(r'https?://github\.com/([^/]+/[^/]+)/blob/(.+?)(\?.*)?$', url)
    if m:
        return f'https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}'
    m = re.match(r'https?://arxiv\.org/html/(\d+\.\d+(?:v\d+)?)', url)
    if m:
        return f'https://arxiv.org/abs/{m.group(1)}'
    return url


def read_page(url: str) -> str:
    """抓取并提取网页正文（带缓存 + 超时 + 截断）。"""
    cached = _cache_get_any("read_page", {"url": url})
    if cached is not None:
        return cached
    rewritten = _rewrite_url(url)
    result, status = _do_read_page(rewritten)
    if status is not None and status in (404, 410):
        _cache_set("read_page", {"url": url}, result, ttl_override=_CACHE_TTL_NOT_FOUND)
    elif status is None and ("无法访问" in result or "超时" in result):
        _cache_set("read_page", {"url": url}, result, ttl_override=_CACHE_TTL_ERROR)
    else:
        _cache_set("read_page", {"url": url}, result)
    return result


def _do_read_page(url: str) -> tuple[str, int | None]:
    """实际执行网页抓取和提取。"""
    html, status = _fetch_url_with_timeout(url)
    if html is None:
        return _error_message(url, status), status
    text = trafilatura.extract(html, include_links=False, include_images=False, include_tables=False)
    if not text:
        return f"{url} 未能提取到有效正文内容。", status
    if len(text) > _PAGE_MAX_CHARS:
        text = text[:_PAGE_MAX_CHARS] + (
            f"\n\n[已压缩：原始结果 {len(text)} 字符，仅显示前 {_PAGE_MAX_CHARS} 字符。]"
        )
    return text, status


def execute(name: str, args: dict) -> str | None:
    if name == "search_web":
        return search_web(args["query"])
    if name == "read_page":
        return read_page(args["url"])
    return None
