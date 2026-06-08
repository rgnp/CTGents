"""测试 _cleanup_tool_results 的上下文门槛：短对话保留工具结果以维持缓存连续。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.llm as llm
from src.cache_context import CacheContext


def _ctx_with_two_tool_results():
    """构造一轮含 2 条工具结果的 ctx。"""
    big = "x" * 2000  # ~600 tokens 估算
    log = [
        {"role": "user", "content": "读两个文件"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": big,
         "_tool_name": "read_file", "_tool_result_compressed": True},
        {"role": "tool", "tool_call_id": "c2", "content": big,
         "_tool_name": "read_file", "_tool_result_compressed": True},
        {"role": "assistant", "content": "两个文件读完了"},
    ]
    return CacheContext(log_msgs=log)


def _tool_count(ctx):
    return sum(1 for m in ctx.log if m.get("role") == "tool")


def _has_archive_summary(ctx):
    return any("已归档" in (m.get("content") or "") for m in ctx.log)


def test_small_context_preserves_tool_results(monkeypatch):
    """上下文远低于门槛 → 不清理，工具结果保留（维持缓存连续）。"""
    monkeypatch.setattr(llm, "MAX_CONTEXT_TOKENS", 100_000)  # 门槛=40k，远大于本 ctx
    ctx = _ctx_with_two_tool_results()
    llm._cleanup_tool_results(ctx)
    assert _tool_count(ctx) == 2, "短对话不应清理工具结果"
    assert not _has_archive_summary(ctx)


def test_large_context_archives_tool_results(monkeypatch):
    """上下文超过门槛 → 清理，工具结果归档为一行摘要。"""
    monkeypatch.setattr(llm, "MAX_CONTEXT_TOKENS", 2000)  # 门槛=800，本 ctx 超过
    ctx = _ctx_with_two_tool_results()
    llm._cleanup_tool_results(ctx)
    assert _tool_count(ctx) == 0, "超门槛应清理工具结果"
    assert _has_archive_summary(ctx), "应留下归档摘要"


def test_below_min_tool_results_not_cleaned(monkeypatch):
    """即使上下文大，单条工具结果也不清理（< _CLEANUP_MIN_TOOL_RESULTS）。"""
    monkeypatch.setattr(llm, "MAX_CONTEXT_TOKENS", 2000)
    ctx = _ctx_with_two_tool_results()
    # 删掉一条，只剩 1 条工具结果
    ctx.log = [m for m in ctx.log if m.get("tool_call_id") != "c2"]
    llm._cleanup_tool_results(ctx)
    assert _tool_count(ctx) == 1, "单条工具结果不值得断缓存清理"
