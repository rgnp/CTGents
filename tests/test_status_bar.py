"""状态条测试：阈值变色 / 突刺判定 / 任务标题 / 异常安全 / Δmiss 基线复位。

只读渲染，不碰缓存/LLM——这里用 monkeypatch 把 count_messages_tokens 与
get_cache_stats 钉成固定值，专测状态条自身的判定逻辑。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import status_bar as sb


class Ctx:
    """假上下文：all/log 都返回一条消息（token 数由 monkeypatch 控制，与内容无关）。"""

    @property
    def all(self):
        return [{"role": "user", "content": "x"}]

    @property
    def log(self):
        return self.all


@pytest.fixture(autouse=True)
def _reset():
    sb.reset()
    yield
    sb.reset()


def _render(monkeypatch, *, tokens, prompt_t=100, hit=60, unfinished=False, current=""):
    """钉死 token 数与缓存统计，渲染一次，返回 HTML 文本（None→空串）。"""
    import src.llm as llm
    import src.tasks as tasks
    import src.tools.tokens as toks
    monkeypatch.setattr(toks, "count_messages_tokens", lambda _m: tokens)
    monkeypatch.setattr(llm, "get_cache_stats",
                        lambda _s: {"total": {"prompt_tokens": prompt_t, "cache_hit_tokens": hit}})
    monkeypatch.setattr(tasks, "has_unfinished", lambda: unfinished)
    monkeypatch.setattr(tasks, "read_current", lambda: current)
    sb.refresh(Ctx(), "sess")
    v = sb.text()
    return v.value if v is not None else ""


def _at_pct(pct: float) -> int:
    from src.config import MAX_CONTEXT_TOKENS
    return int(MAX_CONTEXT_TOKENS * pct / 100)


# ── 上下文充满度变色 ──

def test_normal_no_color(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(20))
    assert "ctx 20%" in out
    assert "ansired" not in out and "ansiyellow" not in out


def test_warn_threshold_yellow(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(58))
    assert "ansiyellow" in out and "ansired" not in out


def test_crit_threshold_red_with_warning(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(63))
    assert "ansired" in out and "压缩临近" in out


# ── 缓存命中率 ──

def test_cache_pct_shown(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), prompt_t=290229, hit=175104)
    assert "cache 60%" in out


def test_cache_hidden_when_no_requests(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), prompt_t=0, hit=0)
    assert "cache" not in out


# ── 当前任务 ──

def test_task_segment_shown(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), unfinished=True,
                  current="# 重构缓存归因\n[ ] step")
    assert "▶ 重构缓存归因" in out


def test_no_task_segment_when_finished(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), unfinished=False, current="# x")
    assert "▶" not in out


# ── Δmiss 突刺 ──

def test_spike_flagged(monkeypatch):
    # 先喂几轮平稳小 miss 建立基线，再来一轮暴涨
    base = 175104
    for cum in (180000, 181000, 182000, 183000):
        _render(monkeypatch, tokens=_at_pct(10), prompt_t=cum + base, hit=base)
    out = _render(monkeypatch, tokens=_at_pct(10), prompt_t=183000 + 30000 + base, hit=base)
    assert "突刺" in out and "ansired" in out


def test_no_delta_on_first_turn(monkeypatch):
    out = _render(monkeypatch, tokens=_at_pct(10), prompt_t=50000, hit=10000)
    assert "Δmiss" not in out  # 首轮基线未建立，不报 Δ


def test_reset_clears_baseline(monkeypatch):
    _render(monkeypatch, tokens=_at_pct(10), prompt_t=50000, hit=10000)
    sb.reset()
    out = _render(monkeypatch, tokens=_at_pct(10), prompt_t=60000, hit=10000)
    assert "Δmiss" not in out  # 复位后视作首轮


# ── 异常安全 ──

def test_refresh_never_raises(monkeypatch):
    class Boom:
        @property
        def all(self):
            raise RuntimeError("boom")
        log = all
    sb.refresh(Boom(), "sess")  # 不抛
    assert sb.text() is None


# ── 纯函数 ──

def test_fmt_k():
    assert sb._fmt_k(500) == "500"
    assert sb._fmt_k(5300) == "5.3k"


def test_task_title_strips_heading():
    assert sb._task_title("## 标题在此\n正文") == "标题在此"
    assert sb._task_title("\n\n  正文行") == "正文行"
    assert sb._task_title("") == ""


def test_is_spike_needs_floor_and_history():
    assert sb._is_spike(1000, [100, 100, 100]) is False  # 低于地板
    assert sb._is_spike(5000, [100, 100]) is False        # 历史不足
    assert sb._is_spike(5000, [100, 100, 100]) is True
