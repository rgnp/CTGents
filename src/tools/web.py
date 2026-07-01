"""Web 工具：search_web + read_page，带 TTL 缓存和超时控制。"""

import json
import re
import time
from typing import Any

import trafilatura
from curl_cffi import requests as curl_requests

from ..config import get_tavily_client

tavily = get_tavily_client()

# -- TTL 缓存 --
_CACHE_TTL_SEARCH = 300    # 搜索结果 5 分钟有效
_CACHE_TTL_PAGE = 600      # 网页成功内容 10 分钟有效
_CACHE_TTL_ERROR = 30      # 网络/超时等临时错误 30 秒有效（快速重试）
_CACHE_TTL_NOT_FOUND = 600 # 404/410 长期缓存
_CACHE_MAX_SIZE = 200      # 最大条目数
_PAGE_MAX_CHARS = 15000    # 页面内容最大字符数（超出截断）
_PAGE_MAX_HTML_BYTES = 300_000  # 下载 HTML 最大字节数（超出截断，避免 trafilatura 解析巨型页面）
_FETCH_TIMEOUT = 8           # HTTP 请求超时秒数（8s 足够，15s 过宽松）
_TAVILY_TIMEOUT = 10         # Tavily API 调用超时秒数

# ── curl_cffi 浏览器伪装轮换 ──
_IMPERSONATES = ["chrome124", "chrome120", "safari17_0", "edge101"]
# 以下状态码表示被反爬拦截，可换伪装重试
_RETRY_CODES: set[int] = {403, 429, 503}

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
            "description": (
                "搜索互联网。depth: fast=快速扫(默认)/deep=递归去重/scholar=限定学术域名。"
                "结果不相关时必须如实告知。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["fast", "deep", "scholar"],
                        "description": (
                            "搜索深度：fast=快速(默认,5条) / deep=advanced递归去重(10条) / "
                            "scholar=限定学术来源(arxiv,openreview等,10条含raw_content)"
                        ),
                    },
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
            "description": "打开 URL 读取网页全文。超出截断时用 offset 续读下一段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "从第几个字符开始读（默认 0 从头开始）。截断提示里会给出续读所需的 offset 值",
                    },
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
        snippet = r.get("content", "")[:300]
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
        except Exception:
            domain = ""

        score = r.get("score")
        score_str = f"   🔢 相关度: {score:.2f}" if score is not None else ""
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
        if score_str:
            lines.append(score_str)
        lines.append(f"   [{domain}] {url}")
        lines.append("")
    return "\n".join(lines)


def _tavily_search_with_timeout(query: str, **tavily_kwargs) -> dict:
    """带超时的 Tavily 搜索调用。额外 kwargs 透传给 tavily.search。"""
    import threading

    result_container: dict | None = None
    error_container: Exception | None = None

    def _run():
        nonlocal result_container, error_container
        try:
            result_container = tavily.search(query, **tavily_kwargs)
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


def search_web(query: str, depth: str = "fast") -> str:
    """搜索互联网并返回格式化结果（带缓存 + 超时 + quota 自愈）。

    depth:
      "fast"   — 快速扫 5 条，适合简单事实查询（默认）
      "deep"   — advanced 递归搜索 10 条，去重更好
      "scholar" — 限定学术域（arxiv, openreview, github, semanticscholar 等），10 条 + raw_content
    """
    cached = _cache_get("search_web", {"query": query, "depth": depth}, _CACHE_TTL_SEARCH)
    if cached is not None:
        return cached

    from tavily import UsageLimitExceededError
    from tavily.errors import ForbiddenError

    # Map depth → Tavily kwargs
    if depth == "deep":
        tavily_kwargs = {"search_depth": "advanced", "max_results": 10, "include_raw_content": True}
    elif depth == "scholar":
        tavily_kwargs = {
            "search_depth": "advanced",
            "max_results": 10,
            "include_domains": [
                "arxiv.org",
                "openreview.net",
                "github.com",
                "semanticscholar.org",
                "paperswithcode.com",
            ],
            "include_raw_content": True,
        }
    else:  # "fast"
        tavily_kwargs = {"max_results": 5}

    try:
        result = _tavily_search_with_timeout(query, **tavily_kwargs)
    except (UsageLimitExceededError, ForbiddenError):
        result = _try_self_heal(query, **tavily_kwargs)
        if result is None:
            return "Tavily API 配额耗尽，所有 key 均已不可用。"
    except TimeoutError:
        return f"搜索超时（{_TAVILY_TIMEOUT}s），请稍后重试或用更具体的关键词。"
    except Exception as e:
        return f"搜索失败: {type(e).__name__}: {e}"

    formatted = format_search_results(result, query)
    if depth == "scholar":
        formatted += (
            "\n🔬 scholar mode：结果限定学术域名（arxiv, openreview, github, "
            "semanticscholar, paperswithcode）。"
        )
    formatted += (
        "\n💡 有价值的发现？用 write_file 保存到 knowledge/search/<主题>.md，"
        "以后 rag_search 可检索。"
    )
    _cache_set("search_web", {"query": query, "depth": depth}, formatted)
    return formatted


def _try_self_heal(query: str, **tavily_kwargs):
    """自愈：重读 .env → 重建 tavily 客户端 → 用所有 key 重试一次。

    当 Tavily 报 quota exceeded / forbidden 时调用。如果磁盘 .env 被另一会话
    加过新 key，这里会捡起来——agent 不需要重启或人工 reload。

    返回 search result dict，失败返回 None。
    """
    global tavily
    import os
    from pathlib import Path

    from dotenv import load_dotenv
    from tavily import InvalidAPIKeyError, UsageLimitExceededError
    from tavily.errors import ForbiddenError as _ForbiddenError

    from ..config import MultiKeyTavilyClient

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path, override=True)
    keys_raw = os.getenv("TAVILY_API_KEYS", os.getenv("TAVILY_API_KEY", ""))
    keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
    if not keys:
        return None
    tavily = MultiKeyTavilyClient(keys)
    try:
        return _tavily_search_with_timeout(query, **tavily_kwargs)
    except (UsageLimitExceededError, _ForbiddenError, InvalidAPIKeyError, TimeoutError):
        return None


def _browser_headers() -> dict:
    """浏览器级请求头（curl_cffi impersonate 已设 TLS 指纹，这里补应用层）。"""
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def _fetch_url_with_timeout(url: str) -> tuple[str | None, int | None]:
    """用 curl_cffi 下载网页，伪装真实浏览器 TLS 指纹，带反反爬轮换。

    逐次尝试 chrome124 → chrome120 → safari17_0 → edge101，
    403/429/503 时自动换伪装重试；全部失败才放弃。

    Returns:
        (html, status_code)
        html=None 表示失败
        status_code 为 HTTP 状态码，网络错误时为 None
    """
    from curl_cffi.requests import RequestsError

    for attempt, imp in enumerate(_IMPERSONATES):
        try:
            resp = curl_requests.get(
                url,
                headers=_browser_headers(),
                impersonate=imp,
                timeout=_FETCH_TIMEOUT,
            )
            status = resp.status_code

            # 被反爬 → 换伪装重试（还有剩余伪装才跳）
            if status in _RETRY_CODES and attempt < len(_IMPERSONATES) - 1:
                continue

            # Content-Type 过滤（非文本类型跳过）
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

            # 取内容 + 编码
            raw = resp.content[:_PAGE_MAX_HTML_BYTES]
            try:
                html = raw.decode(resp.encoding or "utf-8")
            except (UnicodeDecodeError, LookupError):
                html = raw.decode("utf-8", errors="replace")
            return html, status

        except RequestsError:
            if attempt < len(_IMPERSONATES) - 1:
                continue
            return None, None
        except Exception:
            if attempt < len(_IMPERSONATES) - 1:
                continue
            return None, None

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


def read_page(url: str, offset: int = 0) -> str:
    """抓取并提取网页正文（带缓存 + 超时 + 分页 offset）。"""
    cached = _cache_get_any("read_page", {"url": url, "offset": offset})
    if cached is not None:
        return cached
    rewritten = _rewrite_url(url)
    result, status = _do_read_page(rewritten, offset)
    if status is not None and status in (404, 410):
        _cache_set("read_page", {"url": url, "offset": offset}, result, ttl_override=_CACHE_TTL_NOT_FOUND)
    elif status is None and ("无法访问" in result or "超时" in result):
        _cache_set("read_page", {"url": url, "offset": offset}, result, ttl_override=_CACHE_TTL_ERROR)
    else:
        _cache_set("read_page", {"url": url, "offset": offset}, result)
    return result


def _do_read_page(url: str, offset: int = 0) -> tuple[str, int | None]:
    """实际执行网页抓取和提取。offset 指定从第几个字符开始读。"""
    html, status = _fetch_url_with_timeout(url)
    if html is None:
        return _error_message(url, status), status
    text = trafilatura.extract(html, include_links=False, include_images=False, include_tables=False)
    if not text:
        return f"{url} 未能提取到有效正文内容。", status

    total = len(text)

    # offset 越界
    if offset >= total:
        hint = f"（全文共 {total} 字符，offset={offset} 已超出范围，请用 offset=0 从头开始）"
        return hint, status

    # 取 offset 开始的窗口
    chunk = text[offset: offset + _PAGE_MAX_CHARS]
    chunk_end = offset + len(chunk)
    has_more = chunk_end < total

    prefix = ""
    suffix = ""
    if offset > 0:
        prefix = f"[offset={offset}，已跳过前 {offset} 字符]\n\n"
    if has_more:
        suffix = (
            f"\n\n[第 {offset}-{chunk_end} / 共 {total} 字符。"
            f"续读请用 read_page({url!r}, offset={chunk_end})]"
        )
    return prefix + chunk + suffix, status


def execute(name: str, args: dict) -> str | None:
    if name == "search_web":
        return search_web(args["query"], args.get("depth", "fast"))
    if name == "read_page":
        return read_page(args["url"], args.get("offset", 0))
    return None
