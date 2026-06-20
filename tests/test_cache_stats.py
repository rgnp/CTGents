"""缓存统计：单轮累加器（不受会话切换影响）+ 每请求实测尾部 token 入 history。

修的两个 bug：
- footer「输出 0」=快照全局累计做差被 _ensure_session 换/重置指针击穿 → 改单轮累加器。
- /context 尾部「× 请求数」高估 → 改每请求实测 payload 尾部（skip_volatile 的记 0）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import llm


class _Usage:
    def __init__(self, p, h, c):
        self.prompt_tokens = p
        self.completion_tokens = c
        self.prompt_cache_hit_tokens = h
        self.prompt_cache_miss_tokens = p - h


# ── 尾部实测 ──

def test_trailing_system_tokens_counts_only_tail():
    """只数末尾连续 system 段，遇到非 system 即停；开头的前缀 system 不计。"""
    full = [
        {"role": "system", "content": "PREFIX" * 50},   # 前缀，不计
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "system", "content": "TAIL1"},
        {"role": "system", "content": "TAIL2"},
    ]
    tail_only = [
        {"role": "system", "content": "TAIL1"},
        {"role": "system", "content": "TAIL2"},
    ]
    assert llm._trailing_system_tokens(full) == llm._trailing_system_tokens(tail_only)
    assert llm._trailing_system_tokens(full) > 0


def test_trailing_zero_when_last_is_nonsystem():
    msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}]
    assert llm._trailing_system_tokens(msgs) == 0


# ── 单轮累加器 + history 尾部 ──

def test_turn_accum_and_tail_history(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "_STATS_DIR", tmp_path)
    monkeypatch.setattr(llm, "_current_session_id", "")
    monkeypatch.setattr(llm, "_CACHE_STATS", {"pro": dict(llm._EMPTY_STATS)})
    llm.reset_turn_accum()

    with_tail = [{"role": "user", "content": "hi"},
                 {"role": "system", "content": "TAILTAILTAIL"}]
    no_tail = [{"role": "user", "content": "hi"}]

    llm._set_api_usage("pro", _Usage(1000, 800, 50))
    llm._update_cache_stats("pro", with_tail, "sess1")
    llm._set_api_usage("pro", _Usage(1200, 1100, 30))
    llm._update_cache_stats("pro", no_tail, "sess1")

    acc = llm.get_turn_accum()
    assert acc["requests"] == 2
    assert acc["completion_tokens"] == 80          # 50 + 30
    assert acc["miss"] == 200 + 100                # (1000-800)+(1200-1100)

    hist = llm._CACHE_STATS["pro"]["history"]
    assert len(hist) == 2
    assert hist[0]["t"] > 0                         # 带尾部的请求
    assert hist[1]["t"] == 0                        # 无尾部（skip_volatile 模拟）
    # 取证指纹字段齐全 + 每请求输出 token
    assert {"n", "fe", "g", "lcpr", "c"} <= set(hist[0])
    assert hist[0]["c"] == 50 and hist[1]["c"] == 30  # 各请求 completion_tokens
    assert hist[0]["n"] == 1 and hist[1]["n"] == 1  # 各一条非 system 消息
    llm.reset_turn_accum()


# ── 突刺取证：payload 结构指纹 ──

def test_payload_fingerprint_front_hash_stable_when_front_unchanged(monkeypatch):
    """前沿不变 → fe 恒定（即使后面追加新消息）；中段条数 n 随追加增长。"""
    monkeypatch.setattr(llm, "_last_req_time", None)
    base = [{"role": "system", "content": "PREFIX"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"}]   # 前沿 3 条已满
    grown = base + [{"role": "assistant", "content": "A2"},
                    {"role": "system", "content": "TAIL"}]
    fp1 = llm._payload_fingerprint(base)
    fp2 = llm._payload_fingerprint(grown)
    assert fp1["fe"] == fp2["fe"]      # 前沿（最早 3 条非 sys）没变，尾部追加不影响
    assert fp2["n"] > fp1["n"]         # 中段条数增长


def test_payload_fingerprint_front_hash_changes_when_front_rewritten(monkeypatch):
    """靠前旧消息被改写 → fe 变（这正是要抓的"原地改写"信号）。"""
    monkeypatch.setattr(llm, "_last_req_time", None)
    a = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]
    b = [{"role": "user", "content": "Q1-REWRITTEN"}, {"role": "assistant", "content": "A1"}]
    assert llm._payload_fingerprint(a)["fe"] != llm._payload_fingerprint(b)["fe"]


def test_payload_fingerprint_gap_grows(monkeypatch):
    """第二次调用记录与上次的间隔（>=0），首次为 0。"""
    monkeypatch.setattr(llm, "_last_req_time", None)
    assert llm._payload_fingerprint([{"role": "user", "content": "x"}])["g"] == 0.0
    g2 = llm._payload_fingerprint([{"role": "user", "content": "x"}])["g"]
    assert g2 >= 0.0


def test_payload_fingerprint_detects_tools_change(monkeypatch):
    """工具表变化要被 th_chg 抓到（lcpr 只看 messages 会漏）。首次无基准→False。"""
    monkeypatch.setattr(llm, "_last_req_time", None)
    monkeypatch.setattr(llm, "_prev_tools_hash", None)
    msgs = [{"role": "user", "content": "Q"}]
    monkeypatch.setattr(llm, "_last_canonical_request",
                        {"messages": msgs, "tools": [{"function": {"name": "a"}}]})
    fp1 = llm._payload_fingerprint(msgs)
    assert fp1["th_chg"] is False           # 首次没有上次 tools 可比
    # tools 不变 → 仍 False
    fp2 = llm._payload_fingerprint(msgs)
    assert fp2["th_chg"] is False
    # tools 变了 → True
    monkeypatch.setattr(llm, "_last_canonical_request",
                        {"messages": msgs, "tools": [{"function": {"name": "a"}},
                                                     {"function": {"name": "b"}}]})
    fp3 = llm._payload_fingerprint(msgs)
    assert fp3["th_chg"] is True


def test_dump_payload_writes_canonical_and_hashes(monkeypatch, tmp_path):
    """CTG_DUMP_PAYLOADS 开时落盘 canonical_request + 三个哈希 + system_fingerprint。"""
    monkeypatch.setattr(llm, "_DUMP_PAYLOADS", True)
    monkeypatch.setattr(llm, "_PAYLOAD_DIR", tmp_path)
    msgs = [{"role": "system", "content": "P"}, {"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    monkeypatch.setattr(llm, "_last_canonical_request",
                        {"model": "m", "messages": msgs, "tools": tools, "max_tokens": 8192})
    monkeypatch.setattr(llm, "_last_system_fingerprint", "fp_nodeA")
    usage = {"prompt_tokens": 100, "cache_hit_tokens": 0, "cache_miss_tokens": 100,
             "completion_tokens": 5}
    llm._dump_payload("sess1", 7, msgs, usage)

    import json
    rec = json.loads((tmp_path / "sess1" / "req_0007.json").read_text(encoding="utf-8"))
    assert rec["canonical_request"]["tools"] == tools
    assert rec["system_fingerprint"] == "fp_nodeA"
    assert rec["tools_hash"] == llm._hash_obj(tools)
    assert rec["messages_hash"] == llm._hash_obj(msgs)
    assert "request_hash" in rec


def test_reset_turn_accum_zeroes(monkeypatch):
    monkeypatch.setattr(llm, "_current_session_id", "")
    monkeypatch.setattr(llm, "_CACHE_STATS", {"pro": dict(llm._EMPTY_STATS)})
    llm._set_api_usage("pro", _Usage(500, 400, 20))
    llm._update_cache_stats("pro", [{"role": "user", "content": "x"}], "")
    assert llm.get_turn_accum()["requests"] >= 1
    llm.reset_turn_accum()
    assert llm.get_turn_accum() == {"requests": 0, "completion_tokens": 0, "miss": 0}


# ── 新会话 ""→真 id 切换：首请求统计不丢 ──

def test_first_request_carried_over_on_session_assign(monkeypatch, tmp_path):
    """首请求落在 id=""，随后分配真 id——统计应搬过去而非被空文件覆盖丢掉。"""
    monkeypatch.setattr(llm, "_STATS_DIR", tmp_path)
    monkeypatch.setattr(llm, "_current_session_id", "")
    monkeypatch.setattr(llm, "_CACHE_STATS", {"pro": dict(llm._EMPTY_STATS)})

    llm._set_api_usage("pro", _Usage(1000, 0, 50))
    llm._update_cache_stats("pro", [{"role": "user", "content": "x"}], "")        # 首请求 id 空
    llm._set_api_usage("pro", _Usage(1100, 1000, 40))
    llm._update_cache_stats("pro", [{"role": "user", "content": "x"}], "sess-A")  # 切真 id

    st = llm.get_cache_stats("sess-A")
    assert st["total"]["requests"] == 2          # 首请求没丢
    assert st["total"]["prompt_tokens"] == 2100


def test_first_request_carried_over_for_flash(monkeypatch, tmp_path):
    """切到 Flash 时首请求也要搬过去——曾因 carry-over 判据写死 'pro'，Flash 首轮
    被漏判为空：状态栏首次对话不显示 cache、/context 误报"本会话暂无请求"。
    """
    monkeypatch.setattr(llm, "_STATS_DIR", tmp_path)
    monkeypatch.setattr(llm, "_current_session_id", "")
    monkeypatch.setattr(llm, "_CACHE_STATS", {"flash": dict(llm._EMPTY_STATS)})

    llm._set_api_usage("flash", _Usage(1000, 600, 50))
    llm._update_cache_stats("flash", [{"role": "user", "content": "x"}], "")

    st = llm.get_cache_stats("sess-flash")  # 真 id 无文件，应从内存搬运（非只看 pro）
    assert st["total"]["requests"] == 1
    assert st["total"]["cache_hit_tokens"] == 600


def test_resume_existing_session_loads_not_carries(monkeypatch, tmp_path):
    """恢复已有会话（真 id 已有统计文件）→ 正常加载，不把空的无名累计搬上去。"""
    monkeypatch.setattr(llm, "_STATS_DIR", tmp_path)
    # 预置已有会话统计文件
    (tmp_path / "sess-old.json").write_text(
        '{"pro": {"requests": 7, "prompt_tokens": 9000, "completion_tokens": 0,'
        ' "cache_hit_tokens": 8000, "cache_miss_tokens": 1000}}', encoding="utf-8")
    monkeypatch.setattr(llm, "_current_session_id", "")
    monkeypatch.setattr(llm, "_CACHE_STATS", {"pro": dict(llm._EMPTY_STATS)})

    llm._set_api_usage("pro", _Usage(500, 400, 20))
    llm._update_cache_stats("pro", [{"role": "user", "content": "x"}], "sess-old")

    st = llm.get_cache_stats("sess-old")
    assert st["total"]["requests"] == 8          # 7 加载 + 1 本次


# ── /context：无请求时显示提示而非静默省略整段 ──

def test_cache_section_note_when_zero_requests(monkeypatch):
    import src.llm as llm_mod
    from src import commands

    monkeypatch.setattr(llm_mod, "get_cache_stats",
                        lambda _s: {"total": {"requests": 0}, "models": {}})

    class _Ctx:
        log: list = []

    lines: list = []
    commands._append_cache_section(lines, _Ctx(), "sid")
    assert any("API 缓存" in ln for ln in lines)
    assert any("暂无 API 请求" in ln for ln in lines)


# ── 突刺取证判词 ──

def test_spike_verdict_classifies():
    from src.commands import _spike_verdict

    # 冷启动：命中 ≈ 0
    assert "冷启动" in _spike_verdict({"fe": "a"}, None, 1000, 10, 1.0)
    # 健康（>=70%）：无判词，治旧版 #16(90%) 误标
    assert _spike_verdict({"fe": "a", "lcpr": 0.95}, {"fe": "a"}, 1000, 900, 90.0) == ""
    # 前沿变 → 我们改写了旧消息（优先于 lcpr）；要求上一条前沿已定型（n>=3）
    v = _spike_verdict({"fe": "b", "lcpr": 0.1, "n": 5}, {"fe": "a", "n": 4}, 1000, 300, 30.0)
    assert "前沿变" in v
    # 开头几轮前沿还在填（上一条 n<3）→ fe 变属正常，不误报「前沿变」
    v = _spike_verdict({"fe": "b", "lcpr": 0.45, "n": 5}, {"fe": "a", "n": 1}, 9964, 8320, 84.0)
    assert "前沿变" not in v
    # 实命中率 << 本该命中(lcpr) → 服务端吃掉已发过的前缀（答"纯追加为何命中降"）
    v = _spike_verdict({"fe": "a", "lcpr": 0.91}, {"fe": "a"}, 10868, 8320, 77.0)
    assert "服务端吃掉" in v
    # 实命中率 ≈ 本该命中，miss 都在新后缀 → 新内容（预期内，非异常）
    v = _spike_verdict({"fe": "a", "lcpr": 0.66}, {"fe": "a"}, 15487, 10067, 65.0)
    assert "新内容" in v
    # 旧格式（无 lcpr，有前序请求）：信息不足，只标突刺、不强行定因
    assert "突刺" in _spike_verdict({"fe": "a"}, {"fe": "a"}, 1000, 300, 30.0)
    # 首请求（prev=None）大半没命中 = 冷启动，不误判
    assert "冷启动" in _spike_verdict({"fe": "a", "lcpr": 0.0}, None, 11176, 3968, 36.0)


def test_miss_attribution_credits_cold_and_caps_tail_per_request():
    """归因逐请求拆：首请求大面积 miss=冷启动；尾部 miss 每条 ≤ 该条总 miss（不虚高）。"""
    from unittest.mock import patch

    from src import llm
    from src.commands import _append_cache_section
    # #1 冷启动(命中≈前缀) + #2 轮首尾部小 + #3 循环内工具输出(t=0 全归对话增量)
    hist = [
        {"p": 11176, "h": 3968, "t": 600, "n": 1, "fe": "a", "g": 0.0},   # 冷启动 7208
        {"p": 11437, "h": 11136, "t": 600, "n": 3, "fe": "a", "g": 90},   # miss301 尾部截到301内
        {"p": 15487, "h": 10880, "t": 0, "n": 14, "fe": "a", "g": 18},    # miss4607 全对话增量
    ]
    fake = {"total": {"requests": 3, "prompt_tokens": 38100, "cache_hit_tokens": 25984},
            "models": {"pro": {"history": hist}}}
    lines: list = []
    with patch.object(llm, "get_cache_stats", lambda _s: fake):
        _append_cache_section(lines, None, "sid")
    text = "\n".join(lines)
    assert "冷启动    " in text and "7,208" in text          # #1 被认作冷启动
    # 尾部注入不得虚高：#2 尾部 miss ≤301，#3 尾部=0 → 合计 ≤601，远小于 size 累加 1200
    tail_line = next(ln for ln in lines if "尾部注入" in ln)
    tail_val = int(tail_line.split("尾部注入")[1].split()[0].replace(",", ""))
    assert tail_val <= 601
    assert "4,607" in text or "对话增量" in text             # 工具输出进对话增量


def test_isolated_single_shot_not_called_cold_start():
    """隔离单发(会话收割等:n=1、独立上下文)不得被喊成主会话冷启动。

    回归用户实测 #45：对话中段突然 n1、7,524 全 miss，被旧 verdict 标"冷启动"
    吓成"惊天异常"。它其实是另一份上下文串进同一统计流(必 miss、一次性)，主对话
    前缀没丢(前后 n=202/204 连续、100% 命中)。
    """
    from src.commands import _is_isolated_single_shot, _spike_verdict

    iso = {"n": 1, "fe": "z", "lcpr": 0.0}
    main_prev = {"n": 202, "fe": "a"}
    assert _is_isolated_single_shot(iso, main_prev)
    v = _spike_verdict(iso, main_prev, 7524, 0, 0.0)
    assert "隔离单发" in v and "冷启动" not in v
    # 真·主会话冷启动(prev=None)仍判冷启动，不被新分支吞掉
    assert "冷启动" in _spike_verdict({"n": 1, "lcpr": 0.0}, None, 7524, 0, 0.0)
    # 压缩后(n 缩到几十、非 1~2)不误判为隔离单发
    assert not _is_isolated_single_shot({"n": 30}, {"n": 202})


def test_miss_attribution_isolates_single_shot_call():
    """隔离单发的 miss 进『隔离单发』桶，不污染『对话增量』。"""
    from unittest.mock import patch

    from src import llm
    from src.commands import _append_cache_section
    # #1 主对话(大 n) + #2 隔离评分(n=1 全 miss 7524) + #3 主对话回到大 n
    hist = [
        {"p": 151236, "h": 151000, "t": 600, "n": 202, "fe": "a", "g": 3.0},
        {"p": 7524, "h": 0, "t": 0, "n": 1, "fe": "z", "g": 28.8},        # 隔离评分
        {"p": 151308, "h": 151168, "t": 600, "n": 204, "fe": "a", "g": 2.8},
    ]
    fake = {"total": {"requests": 3, "prompt_tokens": 310068, "cache_hit_tokens": 302168},
            "models": {"pro": {"history": hist}}}
    lines: list = []
    with patch.object(llm, "get_cache_stats", lambda _s: fake):
        _append_cache_section(lines, None, "sid")
    text = "\n".join(lines)
    assert "隔离单发" in text and "7,524" in text
    # 7,524 不得落进对话增量
    body_line = next(ln for ln in lines if "对话增量" in ln)
    body_val = int(body_line.split("对话增量")[1].split()[0].replace(",", ""))
    assert body_val < 7524
