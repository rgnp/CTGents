"""任务闭环(outcome loop)— 解析/评分/循环控制 + 评分者隔离不变量。

评分者隔离是这个机制的命门:评分 payload 只许含目标/标准/交付物,worker 的
对话 log 一个字都不许进——否则 worker 能用自己的叙述给自己的交付背书,
闭环退化成自评(citation_audit 不收 assistant 自己的话,同一洞察)。
"""
from __future__ import annotations

import json

import src.outcome as outcome
from src.cache_context import CacheContext
from src.outcome import (
    GradeResult,
    OutcomeSpec,
    _grader_payload,
    _parse_grade,
    _worker_prompt,
    parse_goal,
    run_outcome,
)

# ── /goal 语法解析 ────────────────────────────────────────────

def test_parse_goal_basic():
    spec = parse_goal("写文档 || 含触发时机 | 100字以上")
    assert spec is not None
    assert spec.goal == "写文档"
    assert spec.criteria == ["含触发时机", "100字以上"]
    assert spec.deliverable_path == ""


def test_parse_goal_with_deliverable_path():
    spec = parse_goal("修 bug || 测试全绿说明 | 给出根因 >> docs/fix.md")
    assert spec is not None
    assert spec.deliverable_path == "docs/fix.md"
    assert spec.criteria == ["测试全绿说明", "给出根因"]


def test_parse_goal_missing_criteria_returns_none():
    assert parse_goal("只有目标没有标准") is None
    assert parse_goal("目标 ||   ") is None
    assert parse_goal(" || 只有标准") is None


# ── 评分者隔离(核心不变量) ──────────────────────────────────

def test_grader_payload_isolated_from_worker_log():
    """评分 payload 只含目标/标准/交付物——决不含 worker 对话内容。"""
    spec = OutcomeSpec(goal="目标G", criteria=["标准C1", "标准C2"])
    payload = _grader_payload(spec, "这是交付物D")
    assert "目标G" in payload and "标准C1" in payload and "这是交付物D" in payload
    # payload 由纯函数构造,签名里根本没有 ctx/log —— 结构上隔离
    import inspect
    params = inspect.signature(_grader_payload).parameters
    assert set(params) == {"spec", "deliverable"}, "评分 payload 不得接收 worker log"


def test_run_outcome_grader_never_sees_worker_log(monkeypatch):
    """端到端:worker log 里的噪声标记决不出现在评分调用的消息里。"""
    noise = "SECRET_WORKER_NOISE_8731"
    ctx = CacheContext()
    seen_grader_msgs: list[list[dict]] = []

    def fake_drive(c, user_input):
        c.log.append({"role": "user", "content": user_input})
        c.log.append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]})
        c.log.append({"role": "tool", "tool_call_id": "t1", "content": noise})
        c.log.append({"role": "assistant", "content": "最终交付物文本"})
        return "最终交付物文本"

    def fake_grade_call(spec, deliverable):
        seen_grader_msgs.append([{"role": "user", "content": _grader_payload(spec, deliverable)}])
        return GradeResult(satisfied=True, summary="ok")

    monkeypatch.setattr(outcome, "grade", fake_grade_call)
    spec = OutcomeSpec(goal="目标", criteria=["标准"])
    result = run_outcome(ctx, spec, fake_drive)
    assert result.satisfied
    all_text = json.dumps(seen_grader_msgs, ensure_ascii=False)
    assert noise not in all_text, "worker 工具输出泄漏进评分上下文 = 自评"
    assert "最终交付物文本" in all_text


# ── 循环控制 ──────────────────────────────────────────────────

def _scripted(ctx_replies: list[str], grades: list[GradeResult], monkeypatch):
    """注入脚本化的 worker 回复序列和评分序列。"""
    replies = iter(ctx_replies)
    grade_iter = iter(grades)

    def fake_drive(c, user_input):
        c.log.append({"role": "user", "content": user_input})
        c.log.append({"role": "assistant", "content": next(replies)})

    monkeypatch.setattr(outcome, "grade", lambda *_a: next(grade_iter))
    return fake_drive


def test_loop_stops_when_satisfied(monkeypatch):
    drive = _scripted(["交付v1"], [GradeResult(satisfied=True)], monkeypatch)
    result = run_outcome(CacheContext(), OutcomeSpec("目标", ["标准"]), drive)
    assert result.satisfied and result.iterations == 1


def test_loop_feeds_gaps_back_and_retries(monkeypatch):
    """第一轮未达标 → 差距清单出现在第二轮 worker 输入里。"""
    ctx = CacheContext()
    drive = _scripted(
        ["交付v1", "交付v2"],
        [GradeResult(satisfied=False, gaps=["标准1 — 缺少示例"]), GradeResult(satisfied=True)],
        monkeypatch,
    )
    result = run_outcome(ctx, OutcomeSpec("目标", ["标准1"], max_iterations=3), drive)
    assert result.satisfied and result.iterations == 2
    second_input = [m for m in ctx.log if m["role"] == "user"][1]["content"]
    assert "缺少示例" in second_input, "差距必须回灌给 worker"
    assert "评分未通过" in second_input


def test_loop_stops_at_max_iterations(monkeypatch):
    drive = _scripted(
        ["v1", "v2"],
        [GradeResult(satisfied=False, gaps=["g"]), GradeResult(satisfied=False, gaps=["g"])],
        monkeypatch,
    )
    result = run_outcome(CacheContext(), OutcomeSpec("目标", ["c"], max_iterations=2), drive)
    assert not result.satisfied
    assert result.iterations == 2 and len(result.grades) == 2


# ── 评分解析(保守失败) ──────────────────────────────────────

def test_parse_grade_happy_path():
    raw = json.dumps({"criteria": [{"criterion": "c1", "pass": True, "gap": ""}],
                      "satisfied": True, "summary": "好"}, ensure_ascii=False)
    g = _parse_grade(raw)
    assert g.satisfied and g.gaps == []


def test_parse_grade_collects_gaps():
    raw = json.dumps({"criteria": [
        {"criterion": "c1", "pass": True},
        {"criterion": "c2", "pass": False, "gap": "缺数据"},
    ], "satisfied": False, "summary": "差一条"}, ensure_ascii=False)
    g = _parse_grade(raw)
    assert not g.satisfied
    assert g.gaps == ["c2 — 缺数据"]


def test_parse_grade_satisfied_but_gaps_is_not_satisfied():
    """评分自相矛盾(说 satisfied 但有 fail 项)→ 以逐条结果为准,判不达标。"""
    raw = json.dumps({"criteria": [{"criterion": "c", "pass": False, "gap": "x"}],
                      "satisfied": True}, ensure_ascii=False)
    assert not _parse_grade(raw).satisfied


def test_parse_grade_garbage_fails_closed():
    """解析不动 → 保守判不达标(评分坏了不能放行交付)。"""
    g = _parse_grade("这不是 JSON{{{")
    assert not g.satisfied and g.gaps


def test_parse_grade_strips_code_fence():
    raw = '```json\n{"criteria": [], "satisfied": true, "summary": "s"}\n```'
    assert _parse_grade(raw).satisfied


# ── worker prompt ─────────────────────────────────────────────

def test_worker_prompt_first_iteration_has_goal_and_criteria():
    spec = OutcomeSpec("做X", ["c1", "c2"])
    p = _worker_prompt(spec, 1, None)
    assert "做X" in p and "c1" in p and "c2" in p and "独立评审" in p


def test_worker_prompt_revision_contains_only_gaps():
    spec = OutcomeSpec("做X", ["c1"])
    prev = GradeResult(satisfied=False, gaps=["c1 — 缺证据"])
    p = _worker_prompt(spec, 2, prev)
    assert "缺证据" in p and "做X" not in p, "修订轮只回灌差距,不重复任务全文(缓存/注意力)"
