"""测试 config.py：MultiKeyTavilyClient 多 key 轮换。"""

import pytest
from tavily import InvalidAPIKeyError, UsageLimitExceededError
from tavily.errors import ForbiddenError

from src.config import MultiKeyTavilyClient


def _raise(exc: Exception):
    """Helper：返回一个函数，调用时抛出指定异常。"""
    def raiser(*args, **kwargs):
        raise exc
    return raiser


class TestMultiKeyTavilyClient:
    """MultiKeyTavilyClient 多 API key 自动轮换。"""

    def test_empty_keys_raises(self):
        """空 key 列表应抛出 ValueError。"""
        with pytest.raises(ValueError, match="api_keys 不能为空"):
            MultiKeyTavilyClient([])

    def test_first_key_succeeds_no_rotation(self, monkeypatch):
        """第一个 key 正常工作时不应轮换。"""
        client = MultiKeyTavilyClient(["key-a", "key-b"])

        searches = []

        def fake_search(*args, **kwargs):
            searches.append(client._idx)
            return {"results": [{"title": "ok"}]}

        monkeypatch.setattr(client._client, "search", fake_search)

        result = client.search("test")
        assert result["results"][0]["title"] == "ok"
        assert searches == [0]
        assert client._idx == 0

    def test_rotate_on_usage_limit(self, monkeypatch):
        """第一个 key 额度耗尽 → 自动切第二个 → promote 持久化。"""
        client = MultiKeyTavilyClient(["key-a", "key-b"])

        c0 = client._client
        monkeypatch.setattr(c0, "search", _raise(UsageLimitExceededError("quota")))

        client._rotate()
        c1 = client._client
        monkeypatch.setattr(
            c1, "search",
            lambda *a, **kw: {"results": [{"title": "from b"}]},
        )
        client._idx = 0

        # Mock _promote_current_key：保留状态变更，跳过 .env 磁盘写入
        def mock_promote():
            promoted_key = client._api_keys[client._idx]
            client._api_keys = [promoted_key] + [
                k for i, k in enumerate(client._api_keys) if i != client._idx
            ]
            client._idx = 0
            client._clients = {}
        monkeypatch.setattr(client, "_promote_current_key", mock_promote)

        result = client.search("test")
        assert result["results"][0]["title"] == "from b"
        assert client._idx == 0
        assert client._api_keys == ["key-b", "key-a"]

    def test_all_keys_exhausted(self, monkeypatch):
        """所有 key 耗尽 → 最终抛出 UsageLimitExceededError。"""
        client = MultiKeyTavilyClient(["key-a", "key-b"])

        fail = _raise(UsageLimitExceededError("quota"))
        c0 = client._client
        monkeypatch.setattr(c0, "search", fail)
        client._rotate()
        c1 = client._client
        monkeypatch.setattr(c1, "search", fail)
        client._idx = 0

        with pytest.raises(UsageLimitExceededError):
            client.search("test")

    def test_rotate_on_invalid_key(self, monkeypatch):
        """无效 key 也应触发轮换 → promote 持久化。"""
        client = MultiKeyTavilyClient(["bad-key", "good-key"])

        c0 = client._client
        monkeypatch.setattr(c0, "search", _raise(InvalidAPIKeyError("bad")))

        client._rotate()
        c1 = client._client
        monkeypatch.setattr(
            c1, "search",
            lambda *a, **kw: {"results": [{"title": "from good key"}]},
        )
        client._idx = 0

        # Mock _promote_current_key：保留状态变更，跳过 .env 磁盘写入
        def mock_promote():
            promoted_key = client._api_keys[client._idx]
            client._api_keys = [promoted_key] + [
                k for i, k in enumerate(client._api_keys) if i != client._idx
            ]
            client._idx = 0
            client._clients = {}
        monkeypatch.setattr(client, "_promote_current_key", mock_promote)

        result = client.search("test")
        assert result["results"][0]["title"] == "from good key"
        assert client._idx == 0
        assert client._api_keys == ["good-key", "bad-key"]

    def test_rotate_on_forbidden(self, monkeypatch):
        """ForbiddenError (HTTP 432 额度耗尽) 也应触发轮换 → promote 持久化。"""
        client = MultiKeyTavilyClient(["exhausted", "working"])

        c0 = client._client
        monkeypatch.setattr(c0, "search", _raise(ForbiddenError("plan limit")))

        client._rotate()
        c1 = client._client
        monkeypatch.setattr(
            c1, "search",
            lambda *a, **kw: {"results": [{"title": "from working"}]},
        )
        client._idx = 0

        # Mock _promote_current_key: skip .env write
        def mock_promote():
            promoted_key = client._api_keys[client._idx]
            client._api_keys = [promoted_key] + [
                k for i, k in enumerate(client._api_keys) if i != client._idx
            ]
            client._idx = 0
            client._clients = {}
        monkeypatch.setattr(client, "_promote_current_key", mock_promote)

        result = client.search("test")
        assert result["results"][0]["title"] == "from working"
        assert client._idx == 0
        assert client._api_keys == ["working", "exhausted"]
