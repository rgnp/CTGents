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
    llm.reset_turn_accum()


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
