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

    def test_volatile_system_dropped_pure_append(self):
        """默认(纯追加):log 里的 volatile system 消息(挂尾)整体不进 payload。

        prefix 之后只剩对话(user/assistant/tool),最后一条是对话 → 它就是缓存输入
        结束单元,下轮首请求可靠命中。见 [[ctgents-context-cache]]。
        """
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "PREFIX"}],
            log_msgs=[
                {"role": "user", "content": "Q1"},
                {"role": "system", "content": "TAIL", "_volatile": True},
                {"role": "assistant", "content": "A1"},
            ],
        )
        api = ctx.send()
        roles = [m["role"] for m in api]
        assert roles == ["system", "user", "assistant"]
        assert api[0]["content"] == "PREFIX"
        assert api[-1]["role"] != "system"
        assert all(m.get("content") != "TAIL" for m in api)

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


class TestToolPairingRepair:
    """tool_calls/tool 配对修复：防中断/异常留下光杆 tool_calls 致 API 400 卡死。"""

    def test_dangling_tool_calls_backfilled(self):
        """中断遗留：带 tool_calls 的 assistant 缺 tool 结果 → send() 补占位，不破坏协议。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "user", "content": "Q"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "call_1", "function": {"name": "run"}}]},
                # 中断：缺 call_1 的 tool 结果
            ],
        )
        api = ctx.send()
        # 协议不变量：每个 tool_calls 后必须有对应 tool 结果
        assistant = next(m for m in api if m["role"] == "assistant")
        tool_msgs = [m for m in api if m["role"] == "tool"]
        assert assistant["tool_calls"][0]["id"] == "call_1"
        assert any(t["tool_call_id"] == "call_1" for t in tool_msgs), "缺失结果应被占位补齐"

    def test_partial_results_backfilled(self):
        """多 tool_calls 只回了一部分 → 缺的那个被补齐。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "a", "function": {"name": "f"}},
                    {"id": "b", "function": {"name": "g"}},
                ]},
                {"role": "tool", "tool_call_id": "a", "content": "done"},
                # 缺 b
            ],
        )
        api = ctx.send()
        ids = {m["tool_call_id"] for m in api if m["role"] == "tool"}
        assert ids == {"a", "b"}

    def test_healthy_log_unchanged(self):
        """配对完整的健康 log → send() 不增不删、字节不变（保前缀缓存）。"""
        log = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "x", "function": {"name": "run"}}]},
            {"role": "tool", "tool_call_id": "x", "content": "ok"},
            {"role": "assistant", "content": "答案"},
        ]
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}],
                           log_msgs=log)
        api = ctx.send()
        # prefix(1) + log(4)，无额外占位
        assert len(api) == 5
        assert [m["role"] for m in api] == ["system", "user", "assistant", "tool", "assistant"]


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
