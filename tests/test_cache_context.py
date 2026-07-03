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


class TestToolPairingAdjacency:
    """邻接性强制：tool 结果必须**紧邻** assistant(tool_calls) 之后（DeepSeek 实测协议）。"""

    def test_system_between_assistant_and_tool_reordered(self):
        """失败类钉死（2026-07-03 线上 400 复现）：load_psyche 注入的 system 消息
        插在 assistant(tool_calls) 与 tool 结果中间 → send() 必须把结果挪回紧邻位。
        """
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "user", "content": "加载psyche"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "p1", "function": {"name": "load_psyche"}}]},
                {"role": "system", "content": "【Psyche: x】核心内容"},   # 插队者
                {"role": "tool", "tool_call_id": "p1", "content": "已加载"},
            ],
        )
        api = ctx.send()
        roles = [m["role"] for m in api]
        ai = roles.index("assistant")
        assert api[ai + 1]["role"] == "tool", f"tool 结果必须紧邻 assistant，实际: {roles}"
        assert api[ai + 1]["tool_call_id"] == "p1"
        # 插队 system 不丢，被挪到结果之后
        assert any("Psyche" in (m.get("content") or "") for m in api[ai + 2:])

    def test_result_before_assistant_relocated(self):
        """Tool 结果出现在 assistant 之前（压缩/加载旧会话乱序）→ 挪到紧邻位，不重复。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "tool", "tool_call_id": "x", "content": "result"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "x", "function": {"name": "run"}}]},
            ],
        )
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert len(tools) == 1, "搬运不得产生重复 tool 消息"
        ai = next(i for i, m in enumerate(api) if m["role"] == "assistant")
        assert api[ai + 1]["role"] == "tool" and api[ai + 1]["tool_call_id"] == "x"

    def test_healthy_log_byte_identical(self):
        """健康 log（紧邻配对）→ 输出同对象同顺序，零缓存影响。"""
        log = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "a", "function": {"name": "f"}},
                            {"id": "b", "function": {"name": "g"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"},
            {"role": "assistant", "content": "答案"},
        ]
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}], log_msgs=log)
        assert ctx.send() == ctx.send()
        roles = [m["role"] for m in ctx.send()]
        assert roles == ["system", "user", "assistant", "tool", "tool", "assistant"]

    def test_result_after_assistant_is_fine(self):
        """正常顺序：tool 结果在 assistant 之后 → 不补占位，健康 log 不变。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "y", "function": {"name": "run"}}]},
                {"role": "tool", "tool_call_id": "y", "content": "ok"},
            ],
        )
        api = ctx.send()
        # 应为 prefix(1) + assistant(1) + tool(1) = 3，无额外占位
        assert len(api) == 3
        roles = [m["role"] for m in api]
        assert roles == ["system", "assistant", "tool"]

    def test_multi_assistant_each_has_own_results(self):
        """多个 assistant，各有 tool 结果在后方 → 全部 OK。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "a", "function": {"name": "f"}}]},
                {"role": "tool", "tool_call_id": "a", "content": "r1"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "b", "function": {"name": "g"}}]},
                {"role": "tool", "tool_call_id": "b", "content": "r2"},
            ],
        )
        api = ctx.send()
        assert len(api) == 5  # system + 4
        # 确保没有占位消息
        tool_contents = [m["content"] for m in api if m["role"] == "tool"]
        assert all("已占位" not in c for c in tool_contents)

    def test_crossed_results_relocated_adjacent(self):
        """A、B 两个 assistant 的结果堆在后面 → 各自挪到紧邻位：A,ra,B,rb。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "a", "function": {"name": "f"}}]},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "b", "function": {"name": "g"}}]},
                {"role": "tool", "tool_call_id": "a", "content": "ra"},
                {"role": "tool", "tool_call_id": "b", "content": "rb"},
            ],
        )
        api = ctx.send()
        pairs = [(m["role"], m.get("tool_call_id") or "".join(
            tc["id"] for tc in (m.get("tool_calls") or []))) for m in api[1:]]
        assert pairs == [("assistant", "a"), ("tool", "a"),
                         ("assistant", "b"), ("tool", "b")]
        tool_contents = [m["content"] for m in api if m["role"] == "tool"]
        assert all("已占位" not in c for c in tool_contents)

    def test_live_repro_psyche_mid_loop_then_more_tools(self):
        """完整线上场景：工具循环中 load_psyche 注入 + 后续还有一轮工具调用。"""
        ctx = CacheContext(
            prefix_msgs=[{"role": "system", "content": "sys"}],
            log_msgs=[
                {"role": "user", "content": "干活"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "t1", "function": {"name": "load_psyche"}},
                                {"id": "t2", "function": {"name": "read_file"}}]},
                {"role": "system", "content": "【Psyche: ad】…8KB 核心…"},
                {"role": "tool", "tool_call_id": "t1", "content": "[加载 psyche: ad]"},
                {"role": "tool", "tool_call_id": "t2", "content": "文件内容"},
                {"role": "assistant", "content": "继续",
                 "tool_calls": [{"id": "t3", "function": {"name": "grep_code"}}]},
                {"role": "tool", "tool_call_id": "t3", "content": "命中"},
            ],
        )
        api = ctx.send()
        # 逐个 assistant 校验：其 tool_calls 的结果必须紧跟其后、连续、按声明顺序
        for i, m in enumerate(api):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = [tc["id"] for tc in m["tool_calls"]]
                following = [x.get("tool_call_id") for x in api[i + 1:i + 1 + len(ids)]]
                assert following == ids, (
                    f"assistant@{i} 的结果未紧邻: 期望 {ids}, 实际 {following}")


def _turn(uq: str, tcid: str, tool_content: str) -> list[dict]:
    """造一轮：user 提问 + assistant 工具调用 + tool 结果。"""
    return [
        {"role": "user", "content": uq},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": tcid, "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": tcid, "content": tool_content},
    ]


class TestStaleToolCollapse:
    """中段陈旧工具结果折叠（send() 视图变换，默认 keep_turns=3 / threshold=600）。"""

    @pytest.fixture(autouse=True)
    def _force_normal_band(self, monkeypatch):
        """这些用例测的是 age/threshold 折叠逻辑：舒适区下界置 0、上界置 unreachable，
        让小上下文也恒走「正常档」，与舒适区分档解耦（分档另由 TestFoldPressureBands 测）。
        """
        import dataclasses

        import src.params as params
        monkeypatch.setattr(params, "CONTEXT", dataclasses.replace(
            params.CONTEXT, comfort_zone_low=0, comfort_zone_high=10_000_000))

    def _ctx_with_turns(self, n: int, big: bool = True) -> CacheContext:
        body = ("X" * 700) if big else "small"
        log: list[dict] = []
        for i in range(n):
            log += _turn(f"Q{i}", f"t{i}", f"结果{i}头行\n{body}")
        return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}],
                            log_msgs=log)

    def test_stale_big_tool_collapsed(self):
        """超出最近 3 轮的大工具结果 → 折成 stub，含折叠标记。"""
        ctx = self._ctx_with_turns(4)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert "折叠" in tools[0]["content"], "最老一轮的大结果应被折叠"
        assert "结果0头行" in tools[0]["content"], "应保留首行信号"
        assert len(tools[0]["content"]) < 700

    def test_recent_tool_verbatim(self):
        """最近 3 轮内的工具结果 → 逐字保留。"""
        ctx = self._ctx_with_turns(4)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert "折叠" not in tools[-1]["content"], "最近一轮不该折"
        assert len(tools[-1]["content"]) > 700 - 50

    def test_small_stale_not_collapsed(self):
        """陈旧但小于阈值的结果 → 不折（折了没意义还丢信号）。"""
        ctx = self._ctx_with_turns(4, big=False)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert all("折叠" not in t["content"] for t in tools)

    def test_self_log_untouched(self):
        """只动发出去的副本，self.log 原文不变（落盘/可 recall 不受损）。"""
        ctx = self._ctx_with_turns(4)
        ctx.send()
        # log 里最老的 tool 结果仍是原文全长
        old_tool = next(m for m in ctx.log if m.get("role") == "tool")
        assert len(old_tool["content"]) > 700
        assert "折叠" not in old_tool["content"]

    def test_pairing_preserved_after_collapse(self):
        """折叠保留 tool_call_id → 配对完整性不破。"""
        ctx = self._ctx_with_turns(4)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert tools[0]["tool_call_id"] == "t0"

    def test_stub_has_fetch_hint(self):
        """折叠 stub 带 tool_call_id + fetch_tool_result 取回指引（真实可捞，非假承诺）。"""
        ctx = self._ctx_with_turns(4)
        api = ctx.send()
        stub = next(m["content"] for m in api
                    if m["role"] == "tool" and "折叠" in m["content"])
        assert "fetch_tool_result" in stub
        assert "t0" in stub  # 含 tool_call_id，模型据此取回

    def test_fetch_tool_result_round_trip(self):
        """fetch_tool_result(id) 从 self.log 取回被折叠结果的原文全长。"""
        from src.llm import _fetch_tool_result_from_log
        ctx = self._ctx_with_turns(4)
        out = _fetch_tool_result_from_log(ctx, {"tool_call_id": "t0"})
        assert "结果0头行" in out
        assert "X" * 700 in out  # 原文未丢、全长取回

    def test_fetch_tool_result_missing(self):
        """不存在的 tool_call_id → 友好提示，不抛。"""
        from src.llm import _fetch_tool_result_from_log
        ctx = self._ctx_with_turns(2)
        out = _fetch_tool_result_from_log(ctx, {"tool_call_id": "nope"})
        assert "未找到" in out

    def test_short_session_untouched(self):
        """轮数 < keep_turns → 全部新鲜，一律不折。"""
        ctx = self._ctx_with_turns(2)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert all("折叠" not in t["content"] for t in tools)

    def test_disabled_flag(self, monkeypatch):
        """开关关 → 大陈旧结果也逐字保留。

        注：params 的 _env_* 默认值在类定义时求值一次，实例化不重读 env，故这里用
        dataclasses.replace 造禁用实例、替换单例（不能 setenv，也不能 setattr frozen）。
        """
        import dataclasses

        import src.params as params
        disabled = dataclasses.replace(params.CONTEXT, stale_tool_collapse_enabled=False)
        monkeypatch.setattr(params, "CONTEXT", disabled)
        ctx = self._ctx_with_turns(4)
        api = ctx.send()
        tools = [m for m in api if m["role"] == "tool"]
        assert all("折叠" not in t["content"] for t in tools)


class TestFoldPressureBands:
    """舒适区自适应折叠：低于下界不折 / 区内正常档 / 高于上界紧逼加码。按 pre-fold 绝对体积。"""

    def _ctx(self, n: int) -> CacheContext:
        log: list[dict] = []
        for i in range(n):
            log += _turn(f"Q{i}", f"t{i}", f"头{i}\n" + "X" * 700)
        return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}], log_msgs=log)

    def _patch(self, monkeypatch, **kw):
        import dataclasses

        import src.params as params
        monkeypatch.setattr(params, "CONTEXT", dataclasses.replace(params.CONTEXT, **kw))

    def _n_folded(self, ctx: CacheContext) -> int:
        return sum("折叠" in m["content"] for m in ctx.send() if m["role"] == "tool")

    def test_below_comfort_low_no_fold(self, monkeypatch):
        """体积 < comfort_zone_low → 完全不折，即便有 3 轮前的大结果（舒适区下方、有空间）。"""
        self._patch(monkeypatch, comfort_zone_low=10_000_000)   # 永远到不了 → 恒不折
        assert self._n_folded(self._ctx(5)) == 0

    def test_squeeze_folds_more_than_normal(self, monkeypatch):
        """高于上界的紧逼档比区内正常档折得多：keep_turns 1 vs 3 → 更多轮被折。"""
        self._patch(monkeypatch, comfort_zone_low=0, comfort_zone_high=10_000_000,
                    stale_tool_keep_turns=3, stale_tool_collapse_threshold=600)
        normal = self._n_folded(self._ctx(5))               # 正常档 keep=3 → 折最老 2 轮
        self._patch(monkeypatch, comfort_zone_low=0, comfort_zone_high=0,
                    stale_tool_squeeze_keep_turns=1, stale_tool_squeeze_threshold=300)
        squeeze = self._n_folded(self._ctx(5))              # 紧逼档 keep=1 → 折最老 4 轮
        assert squeeze > normal, f"紧逼应折更多，实得 紧逼={squeeze} 正常={normal}"

    def test_gate_uses_real_prefold_size(self, monkeypatch):
        """同一上下文：下界设在其体积之下→折；设在之上→不折（绝对门按真实体积生效）。"""
        # 折叠看的是 log（不含 prefix）；按 log 内容估，与 _collapse 内部口径一致。
        approx = sum(len(m.get("content") or "") for m in self._ctx(5).log) // 4
        self._patch(monkeypatch, comfort_zone_low=approx // 2, comfort_zone_high=10_000_000)
        assert self._n_folded(self._ctx(5)) > 0, "体积超下界应折"
        self._patch(monkeypatch, comfort_zone_low=approx * 2, comfort_zone_high=10_000_000)
        assert self._n_folded(self._ctx(5)) == 0, "体积低于下界应保真不折"


class TestLiveContextTokensCoherence:
    """压缩/熔断判定量"实际发送体积"（折叠后），而非 self.log 原文全长。"""

    def test_live_tokens_below_raw_when_stale_collapsed(self, monkeypatch):
        """有陈旧大工具结果时，_live_context_tokens < 原文计数（折叠生效）。"""
        import dataclasses

        import src.llm as llm
        import src.params as params
        from src.tools.tokens import count_messages_tokens
        monkeypatch.setattr(params, "CONTEXT", dataclasses.replace(  # 小上下文也走正常档
            params.CONTEXT, comfort_zone_low=0, comfort_zone_high=10_000_000))
        log: list[dict] = []
        for i in range(5):  # 5 轮 → 老的几轮工具结果会被折叠
            log += _turn(f"Q{i}", f"t{i}", "结果头\n" + "Z" * 3000)
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}],
                           log_msgs=log)
        live = llm._live_context_tokens(ctx)
        raw = count_messages_tokens(ctx.all)
        assert live < raw, "折叠后发送体积应小于原文全长"

    def test_live_tokens_legacy_list(self):
        """传 flat list（legacy）→ 直接按其计数，不报错。"""
        import src.llm as llm
        from src.tools.tokens import count_messages_tokens
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        assert llm._live_context_tokens(msgs) == count_messages_tokens(msgs)


class TestCompactionEffectiveness:
    """防抖计数器按真实 token 节省更新（不再用 len(msgs)*80 假数）。"""

    def test_low_saving_increments(self):
        import src.llm as llm
        llm._ineffective_compression_count = 0
        llm._update_compaction_effectiveness(before=1000, after=950)  # 省 5% < 10%
        assert llm._ineffective_compression_count == 1

    def test_good_saving_resets(self):
        import src.llm as llm
        llm._ineffective_compression_count = 2
        llm._update_compaction_effectiveness(before=1000, after=700)  # 省 30% >= 10%
        assert llm._ineffective_compression_count == 0

    def test_debounce_rearm_on_growth(self):
        """单向锁修复：防抖关停后，用量涨过重试水位 → 重新允许压缩、计数复位。"""
        import src.llm as llm
        try:
            llm._ineffective_compression_count = 0
            llm._compact_skip_floor = 0
            floor_used = llm.CONTEXT.comfort_zone_high + 1000
            # 连续 2 次低效 → 防抖关停 + 记下放弃水位
            llm._update_compaction_effectiveness(before=floor_used, after=floor_used - 100)
            llm._update_compaction_effectiveness(before=floor_used, after=floor_used - 100)
            assert llm._ineffective_compression_count == 2
            assert llm._compact_skip_floor == floor_used - 100
            # 同水位 → 仍防抖，不压
            assert llm._should_compact(floor_used) is False
            # 涨过重试水位 → 重新武装、计数复位
            grown = llm._compact_skip_floor + int(llm.MAX_CONTEXT_TOKENS * llm._COMPACT_REARM_GROWTH) + 1
            assert llm._should_compact(grown) is True
            assert llm._ineffective_compression_count == 0
        finally:
            llm._ineffective_compression_count = 0
            llm._compact_skip_floor = 0


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
