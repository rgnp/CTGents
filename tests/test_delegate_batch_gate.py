"""同批多 delegate 只放行第一个——_handle_tool_results 的批次闸。

失败类（2026-07-20 实测）：模型一条消息连发 3 个 delegate，串行阻塞主线几十分钟。
闸在解析后/执行前把第 2+ 个 delegate 预填拒绝结果：不执行 worker，但 tool 结果
照常写 log（API 的 tool_calls↔tool 配对契约不破坏）。
"""

import json

import src.llm as llm
from src.cache_context import CacheContext


def _tc(name: str, args: dict, id_: str) -> dict:
    return {"id": id_, "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _run_batch(monkeypatch, calls):
    executed: list[str] = []

    def fake_batch(items):
        executed.extend(item[1] for item in items)
        return [f"ok:{item[1]}" for item in items]

    monkeypatch.setattr(llm, "_execute_tool_batch", fake_batch)
    ctx = CacheContext()
    llm._handle_tool_results(ctx, calls, {}, lambda *_a: None, None, 0)
    return executed, ctx


def test_only_first_delegate_in_batch_executes(monkeypatch):
    executed, ctx = _run_batch(monkeypatch, [
        _tc("delegate", {"brief": "a", "output_file": "knowledge/a.md"}, "1"),
        _tc("delegate", {"brief": "b", "output_file": "knowledge/b.md"}, "2"),
        _tc("delegate", {"brief": "c", "output_file": "knowledge/c.md"}, "3"),
    ])
    assert executed.count("delegate") == 1, "同批只许执行一个 delegate"
    tool_msgs = [m for m in ctx.log if m.get("role") == "tool"]
    assert len(tool_msgs) == 3, "三个 tool_call 必须都有 tool 结果（API 配对契约）"
    rejected = [m for m in tool_msgs if "本批已有一个 delegate" in str(m.get("content"))]
    assert {m["tool_call_id"] for m in rejected} == {"2", "3"}
    assert "正道" in rejected[0]["content"], "拒绝文案要教正道（下一轮携带结果再派）"


def test_other_tools_in_batch_unaffected(monkeypatch):
    executed, ctx = _run_batch(monkeypatch, [
        _tc("read_file", {"path": "a.md"}, "1"),
        _tc("delegate", {"brief": "a", "output_file": "knowledge/a.md"}, "2"),
        _tc("read_file", {"path": "b.md"}, "3"),
    ])
    assert executed == ["read_file", "delegate", "read_file"], "非 delegate 工具照常执行"


def test_single_delegate_passes_untouched(monkeypatch):
    executed, ctx = _run_batch(monkeypatch, [
        _tc("delegate", {"brief": "a", "output_file": "knowledge/a.md"}, "1"),
    ])
    assert executed == ["delegate"]
    tool_msgs = [m for m in ctx.log if m.get("role") == "tool"]
    assert "本批已有一个 delegate" not in str(tool_msgs[0].get("content"))
