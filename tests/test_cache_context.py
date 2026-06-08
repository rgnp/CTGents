"""CacheContext 三段式上下文管理器测试。"""

import pytest

from src.cache_context import CacheContext, PrefixIntegrityError, compute_prefix_hash


class TestCacheContextBasics:
    """基本创建和属性测试。"""

    def test_empty(self):
        ctx = CacheContext()
        assert ctx.stats()["total"]["messages"] == 0
        assert ctx.send() == []

    def test_prefix_only(self):
        ctx = CacheContext(prefix_msgs=[
            {"role": "system", "content": "rule1"},
            {"role": "system", "content": "rule2"},
        ])
        assert len(ctx.prefix) == 2
        assert ctx.stats()["prefix"]["messages"] == 2
        api = ctx.send()
        assert len(api) == 2
        assert api[0] == {"role": "system", "content": "rule1"}

    def test_with_log(self):
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[{"role": "user", "content": "hello"}],
        )
        assert ctx.stats()["log"]["messages"] == 1
        assert len(ctx) == 2

    def test_all_property(self):
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "A"}],
            log_msgs=[{"role": "user", "content": "B"}],
        )
        assert len(ctx.all) == 2
        assert ctx.all[0]["content"] == "A"
        assert ctx.all[1]["content"] == "B"


class TestSendMethod:
    """send() 构建 API 消息测试。"""

    def test_volatile_filtered(self):
        """_volatile 标记的 prefix 消息仍发送给 API（仅影响持久化过滤）。"""
        ctx = CacheContext(prefix_msgs=[
            {"role": "system", "content": "sys", "_volatile": True},
        ])
        api = ctx.send()
        # _volatile 不影响 API 发送，只影响 session 持久化
        assert len(api) == 1

    def test_log_system_messages_in_order(self):
        """Log 中的 system 消息应在对话之后，避免破坏前缀缓存。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "PREFIX"}],
            log_msgs=[
                {"role": "user", "content": "Q1"},
                {"role": "system", "content": "LOG_SYS"},
                {"role": "assistant", "content": "A1"},
            ],
        )
        api = ctx.send()
        roles = [m["role"] for m in api]
        assert roles == ["system", "user", "assistant", "system"]
        assert api[0]["content"] == "PREFIX"
        assert api[1]["content"] == "Q1"
        assert api[3]["content"] == "LOG_SYS"

    def test_scratch_not_in_api(self):
        """Scratch 消息不应出现在 send() 输出中。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
        )
        ctx.scratch.append({"role": "think", "content": "thinking..."})
        api = ctx.send()
        assert all(m.get("role") != "think" for m in api)
        assert ctx.stats()["scratch"]["messages"] == 1

    def test_tool_calls_preserved(self):
        """Assistant 消息中的 tool_calls 应保留。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[{
                "role": "assistant",
                "content": "ok",
                "tool_calls": [{"id": "1", "function": {"name": "run"}}],
            }],
        )
        api = ctx.send()
        assert api[1]["tool_calls"] == [{"id": "1", "function": {"name": "run"}}]

    def test_tool_call_id_preserved(self):
        """Tool 消息中的 tool_call_id 应保留。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[{"role": "tool", "tool_call_id": "abc", "content": "result"}],
        )
        api = ctx.send()
        assert api[1]["tool_call_id"] == "abc"


class TestPrefixIntegrity:
    """前缀完整性校验测试。"""

    def test_hash_stable(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "hello"}])
        assert ctx.prefix_hash == ctx.prefix_hash  # 幂等

    def test_validation_passes(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "ok"}])
        ctx.send(validate=True)  # 不应抛异常

    def test_validation_fails_on_modification(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "ok"}])
        ctx.prefix[0]["content"] = "tampered"
        with pytest.raises(PrefixIntegrityError):
            ctx.send(validate=True)

    def test_validation_skipped(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "ok"}])
        ctx.prefix[0]["content"] = "tampered"
        ctx.send(validate=False)  # 不应抛异常


class TestOperations:
    """clear / rebuild / append 操作测试。"""

    def test_clear_log(self):
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "pre"}],
            log_msgs=[{"role": "user", "content": "hello"}],
        )
        ctx.clear_log()
        assert len(ctx.log) == 0
        assert len(ctx.prefix) == 1  # prefix 不受影响

    def test_rebuild_prefix(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "old"}])
        old_hash = ctx.prefix_hash
        ctx.rebuild_prefix([{"role": "system", "content": "new"}])
        assert ctx.prefix_hash != old_hash
        assert ctx.prefix[0]["content"] == "new"

    def test_append_to_prefix(self):
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "first"}])
        ctx.append_to_prefix({"role": "system", "content": "second"})
        assert len(ctx.prefix) == 2
        assert ctx.prefix[1]["content"] == "second"

    def test_clear_scratch(self):
        ctx = CacheContext()
        ctx.scratch.append({"role": "think", "content": "x"})
        ctx.clear_scratch()
        assert len(ctx.scratch) == 0

    def test_last_user_content(self):
        ctx = CacheContext(log_msgs=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ])
        assert ctx.last_user_content() == "q2"

    def test_last_user_content_none(self):
        ctx = CacheContext()
        assert ctx.last_user_content() is None


class TestComputePrefixHash:
    """向后兼容的 compute_prefix_hash 函数测试。"""

    def test_flat_list(self):
        msgs = [
            {"role": "system", "content": "s1"},
            {"role": "system", "content": "s2"},
            {"role": "user", "content": "hello"},
        ]
        h, chars, tokens = compute_prefix_hash(msgs)
        assert len(h) == 16
        assert chars > 0
        assert tokens > 0

    def test_no_system(self):
        msgs = [{"role": "user", "content": "hello"}]
        h, chars, tokens = compute_prefix_hash(msgs)
        assert len(h) == 16
        assert chars == 0
        assert tokens == 0
