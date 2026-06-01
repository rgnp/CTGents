"""测试 web.py：TTL 缓存、超时控制、页面截断、错误缓存策略。"""

import time
import pytest


class TestWebCache:
    """测试 search_web 和 read_page 的 TTL 缓存。"""

    def test_search_web_cache_hit(self, monkeypatch):
        """重复查询应命中缓存，不重复调 API。"""
        from src.tools.web import search_web, _web_cache, get_web_cache_stats

        _web_cache.clear()

        call_count = 0

        def fake_search(query, max_results=5):
            nonlocal call_count
            call_count += 1
            return {
                "results": [
                    {
                        "title": f"Result {call_count}",
                        "url": "https://example.com",
                        "content": "snippet",
                    }
                ]
            }

        monkeypatch.setattr("src.tools.web.tavily.search", fake_search)

        r1 = search_web("test query")
        r2 = search_web("test query")

        assert call_count == 1  # 第二次走缓存
        assert r1 == r2
        assert "Result 1" in r1
        assert get_web_cache_stats()["hits"] == 1

    def test_search_web_cache_expiry(self, monkeypatch):
        """过期查询应重新调 API。"""
        from src.tools.web import search_web, _web_cache, _CACHE_TTL_SEARCH

        _web_cache.clear()

        call_count = 0

        def fake_search(query, max_results=5):
            nonlocal call_count
            call_count += 1
            return {"results": []}

        monkeypatch.setattr("src.tools.web.tavily.search", fake_search)

        r1 = search_web("fresh")
        assert call_count == 1

        # 手动让缓存过期
        import json
        key = f'search_web:{json.dumps({"query": "fresh"}, sort_keys=True, ensure_ascii=False)}'
        _web_cache[key]["t"] = time.time() - _CACHE_TTL_SEARCH - 1

        r2 = search_web("fresh")
        assert call_count == 2  # 过期后重新请求

    def test_read_page_cache_hit(self, monkeypatch):
        """重复读同一 URL 应命中缓存。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        call_count = 0

        def fake_fetch(url):
            nonlocal call_count
            call_count += 1
            return "<html><body><p>Cached page content</p></body></html>", 200

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        r1 = read_page("https://example.com/test")
        r2 = read_page("https://example.com/test")

        assert call_count == 1
        assert "Cached page content" in r1
        assert r1 == r2

    def test_read_page_different_url_no_cache(self, monkeypatch):
        """不同 URL 不走缓存。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        call_count = 0

        def fake_fetch(url):
            nonlocal call_count
            call_count += 1
            return f"<html><body>{url}</body></html>", 200

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        read_page("https://a.com")
        read_page("https://b.com")

        assert call_count == 2

    def test_read_page_404_cached_long(self, monkeypatch):
        """404 错误应长期缓存（不反复重试）。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        call_count = 0

        def fake_fetch(url):
            nonlocal call_count
            call_count += 1
            return None, 404

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        r1 = read_page("https://example.com/missing")
        r2 = read_page("https://example.com/missing")

        assert call_count == 1
        assert "404" in r1
        assert r1 == r2

    def test_read_page_timeout_cached_short(self, monkeypatch):
        """网络超时应短缓存（允许快速重试）。"""
        from src.tools.web import read_page, _web_cache, _CACHE_TTL_ERROR

        _web_cache.clear()

        call_count = 0

        def fake_fetch(url):
            nonlocal call_count
            call_count += 1
            return None, None  # 模拟网络超时

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        r1 = read_page("https://slow.example.com")
        assert call_count == 1
        assert "超时" in r1 or "DNS" in r1

        # 短缓存未过期 → 应走缓存
        r2 = read_page("https://slow.example.com")
        assert call_count == 1  # 还在缓存中

        # 手动让短缓存过期
        import json
        key = f'read_page:{json.dumps({"url": "https://slow.example.com"}, sort_keys=True, ensure_ascii=False)}'
        _web_cache[key]["t"] = time.time() - _CACHE_TTL_ERROR - 1

        r3 = read_page("https://slow.example.com")
        assert call_count == 2  # 过期后重新请求


class TestPageTruncation:
    """测试页面内容截断。"""

    def test_long_page_truncated(self, monkeypatch):
        """超过 _PAGE_MAX_CHARS 的内容应被截断。"""
        from src.tools.web import read_page, _web_cache, _PAGE_MAX_CHARS

        _web_cache.clear()

        long_text = "A" * (_PAGE_MAX_CHARS + 500)
        html = f"<html><body><p>{long_text}</p></body></html>"

        monkeypatch.setattr(
            "src.tools.web._fetch_url_with_timeout", lambda u: (html, 200)
        )

        result = read_page("https://example.com/big")
        assert len(result) <= _PAGE_MAX_CHARS + 200
        assert "[已压缩" in result

    def test_short_page_not_truncated(self, monkeypatch):
        """短页面不被截断。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        short = "Hello world"
        html = f"<html><body><p>{short}</p></body></html>"

        monkeypatch.setattr(
            "src.tools.web._fetch_url_with_timeout", lambda u: (html, 200)
        )

        result = read_page("https://example.com/small")
        assert "Hello world" in result
        assert "[已压缩" not in result


class TestCacheEviction:
    """测试缓存淘汰。"""

    def test_cache_eviction(self, monkeypatch):
        """超过最大容量应淘汰最旧条目。"""
        from src.tools.web import search_web, _web_cache, _CACHE_MAX_SIZE, get_web_cache_stats

        _web_cache.clear()

        call_count = 0

        def fake_search(query, max_results=5):
            nonlocal call_count
            call_count += 1
            return {"results": [{"title": query, "url": "x", "content": "x"}]}

        monkeypatch.setattr("src.tools.web.tavily.search", fake_search)

        for i in range(_CACHE_MAX_SIZE + 20):
            search_web(f"query-{i}")

        stats = get_web_cache_stats()
        assert stats["entries"] <= _CACHE_MAX_SIZE


class TestUrlRewrite:
    """测试 URL 重写逻辑。"""

    def test_github_blob_to_raw(self):
        """GitHub blob URL 应重写为 raw.githubusercontent.com。"""
        from src.tools.web import _rewrite_url

        url = "https://github.com/user/repo/blob/main/path/to/file.py"
        expected = "https://raw.githubusercontent.com/user/repo/main/path/to/file.py"
        assert _rewrite_url(url) == expected

    def test_github_blob_with_query(self):
        """带查询参数的 GitHub blob URL 应去掉查询参数。"""
        from src.tools.web import _rewrite_url

        url = "https://github.com/user/repo/blob/main/file.py?ref=main"
        expected = "https://raw.githubusercontent.com/user/repo/main/file.py"
        assert _rewrite_url(url) == expected

    def test_github_repo_root(self):
        """GitHub 仓库主页 URL 不应被重写。"""
        from src.tools.web import _rewrite_url

        url = "https://github.com/user/repo"
        assert _rewrite_url(url) == url

    def test_arxiv_html_to_abs(self):
        """arxiv HTML 版 URL 应重写为 abstract 页面（保留版本号）。"""
        from src.tools.web import _rewrite_url

        url = "https://arxiv.org/html/2501.11260v3"
        expected = "https://arxiv.org/abs/2501.11260v3"
        assert _rewrite_url(url) == expected

    def test_arxiv_abs_unchanged(self):
        """arxiv abstract 页面 URL 不应被重写。"""
        from src.tools.web import _rewrite_url

        url = "https://arxiv.org/abs/2501.11260"
        assert _rewrite_url(url) == url

    def test_normal_url_unchanged(self):
        """普通 URL 不应被重写。"""
        from src.tools.web import _rewrite_url

        url = "https://blog.csdn.net/CV_Autobot/article/details/155106902"
        assert _rewrite_url(url) == url

    def test_read_page_rewrites_github_blob(self, monkeypatch):
        """read_page 遇到 GitHub blob URL 应走 raw.githubusercontent.com。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        requested_urls = []

        def fake_fetch(url):
            requested_urls.append(url)
            return ("<html><body>file content: print('hello')</body></html>", 200)

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        result = read_page("https://github.com/user/project/blob/main/code.py")

        assert len(requested_urls) == 1
        assert "raw.githubusercontent.com" in requested_urls[0]
        assert "print('hello')" in result

    def test_read_page_rewrites_arxiv_html(self, monkeypatch):
        """read_page 遇到 arxiv HTML URL 应走 abs 页面。"""
        from src.tools.web import read_page, _web_cache

        _web_cache.clear()

        requested_urls = []

        def fake_fetch(url):
            requested_urls.append(url)
            return ("<html><body>arXiv abstract content</body></html>", 200)

        monkeypatch.setattr("src.tools.web._fetch_url_with_timeout", fake_fetch)

        result = read_page("https://arxiv.org/html/2402.13243v2")

        assert len(requested_urls) == 1
        assert "/abs/" in requested_urls[0]
        assert "arXiv abstract" in result

