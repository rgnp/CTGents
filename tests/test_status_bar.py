"""状态显示测试：状态条阈值变色 / 任务 / 缓存，每轮 footer 增量 + 突刺，异常安全。

纯只读——monkeypatch 把 count_messages_tokens 与 get_cache_stats 钉成固定值，
专测状态显示自身的判定逻辑。per-turn 增量经 note_turn_start()/note_turn_end()。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import status_bar as sb


class Ctx:
    @property
    def all(self):
        return [{"role": "user", "content": "x"}]

    @property
    def log(self):
        return self.all


class Stats:
    """可变累计统计，模拟 get_cache_stats('').total 随请求增长。"""

    def __init__(self):
        self.requests = self.completion = self.prompt = self.hit = 0

    def totals(self):
        return {"total": {"requests": self.requests, "completion_tokens": self.completion,
                          "prompt_tokens": self.prompt, "cache_hit_tokens": self.hit}}


@pytest.fixture(autouse=True)
def _reset():
    sb.reset()
    yield
    sb.reset()


def _at_pct(pct: float) -> int:
    from src.config import MAX_CONTEXT_TOKENS
    return int(MAX_CONTEXT_TOKENS * pct / 100)


def _patch_stats(monkeypatch, stats: Stats):
    import src.llm as llm
    monkeypatch.setattr(llm, "get_cache_stats", lambda _s="": stats.totals())


def _render(monkeypatch, *, tokens, stats: Stats, unfinished=False, current=""):
    import src.tasks as tasks
    import src.tools.tokens as toks
    _patch_stats(monkeypatch, stats)
    # 状态条用 count_context_tokens(消息+工具 schema)——直接桩这个，测试精确控制最终占用值
    monkeypatch.setattr(toks, "count_context_tokens", lambda _m: tokens)
    monkeypatch.setattr(tasks, "has_unfinished", lambda: unfinished)
    monkeypatch.setattr(tasks, "read_current", lambda: current)
    sb.refresh(Ctx(), "sess")
    v = sb.text()
    return v.value if v is not None else ""


# ── 状态条：上下文充满度变色 ──

def test_normal_no_color(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(20), stats=Stats())
    assert "ctx 20%" in out
    assert "ansired" not in out and "ansiyellow" not in out


def test_warn_threshold_yellow(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(58), stats=Stats())
    assert "ansiyellow" in out and "ansired" not in out


def test_crit_threshold_red_with_warning(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(63), stats=Stats())
    assert "ansired" in out and "压缩临近" in out


# ── 状态条：缓存命中率 / 任务 ──

def test_cache_pct_shown(monkeypatch):
    s = Stats()
    s.prompt, s.hit = 290229, 175104
    out = _render(monkeypatch, tokens=_at_pct(10), stats=s)
    assert "cache 60%" in out


def test_cache_hidden_when_no_requests(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats())
    assert "cache" not in out


def test_task_segment_shown(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats(),
                  unfinished=True, current="# 重构缓存归因\n[ ] step")
    assert "▶ 重构缓存归因" in out


def test_no_task_segment_when_finished(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats(),
                  unfinished=False, current="# x")
    assert "▶" not in out


# ── 每轮 footer：增量走 llm 单轮累加器（不受会话切换影响）──

def _set_accum(monkeypatch, *, requests, completion, miss):
    """钉死 llm 单轮累加器返回值（footer/Δmiss 读它，不再快照全局累计做差）。"""
    import src.llm as llm
    monkeypatch.setattr(llm, "reset_turn_accum", lambda: None)
    monkeypatch.setattr(llm, "get_turn_accum",
                        lambda: {"requests": requests, "completion_tokens": completion, "miss": miss})


def _run_turn(monkeypatch, *, requests=1, completion=100, miss=500):
    _set_accum(monkeypatch, requests=requests, completion=completion, miss=miss)
    sb.note_turn_start()
    return sb.note_turn_end()


def test_footer_reports_time_output_requests(monkeypatch):
    footer = _run_turn(monkeypatch, requests=4, completion=1847, miss=5000)
    assert footer is not None
    assert footer.strip().startswith("本轮")
    assert "输出 1,847 tok" in footer
    assert "请求 4" in footer
    assert "miss 5.0k" in footer
    assert "耗时" in footer and "s" in footer  # 含耗时


def test_footer_none_without_start():
    assert sb.note_turn_end() is None        # 没起点（set=False）


# ── 突刺：per-turn miss 暴涨 ──

def test_footer_spike_flagged(monkeypatch):
    for _ in range(3):
        _run_turn(monkeypatch, miss=500)     # 建立平稳基线
    footer = _run_turn(monkeypatch, miss=50000)
    assert "突刺" in footer


def test_no_spike_on_steady(monkeypatch):
    footer = None
    for _ in range(4):
        footer = _run_turn(monkeypatch, miss=500)
    assert "突刺" not in footer


# ── 状态条 Δmiss 复用 last_turn ──

def test_bar_delta_from_last_turn(monkeypatch):
    _run_turn(monkeypatch, requests=1, completion=0, miss=5300)
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats())
    assert "Δmiss 5.3k" in out


def test_bar_no_delta_before_any_turn(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats())
    assert "Δmiss" not in out


def test_reset_clears_last_turn(monkeypatch):
    _run_turn(monkeypatch, miss=5000)
    sb.reset()
    out = _render(monkeypatch, tokens=_at_pct(10), stats=Stats())
    assert "Δmiss" not in out


# ── 异常安全 ──

def test_refresh_never_raises(monkeypatch):
    class Boom:
        @property
        def all(self):
            raise RuntimeError("boom")
        log = all
    sb.refresh(Boom(), "sess")
    assert sb.text() is None


def test_note_turn_end_never_raises(monkeypatch):
    import src.llm as llm
    monkeypatch.setattr(llm, "reset_turn_accum", lambda: None)
    monkeypatch.setattr(llm, "get_turn_accum",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    sb.note_turn_start()
    assert sb.note_turn_end() is None   # get_turn_accum 抛 → 吞掉返回 None


# ── 纯函数 ──

def test_fmt_k():
    assert sb._fmt_k(500) == "500"
    assert sb._fmt_k(5300) == "5.3k"


def test_task_title_strips_heading():
    assert sb._task_title("## 标题在此\n正文") == "标题在此"
    assert sb._task_title("\n\n  正文行") == "正文行"
    assert sb._task_title("") == ""


def test_is_spike_needs_floor_and_history():
    assert sb._is_spike(1000, [100, 100, 100]) is False
    assert sb._is_spike(5000, [100, 100]) is False
    assert sb._is_spike(5000, [100, 100, 100]) is True
