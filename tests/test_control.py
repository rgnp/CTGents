"""控制工具（task_done / need_user）+ run_conversation 显式停止信号接线。

设计 #1：把"本轮结束"从隐式（模型不调工具）变成 agent 显式调控制工具，
run_conversation 检测到就置 ctx.control_signal 并结束本轮。C16：新接线即新不变量。
"""
from __future__ import annotations

import pytest

import src.llm as llm
import src.tasks as tasks
from src.cache_context import CacheContext
from src.tools.control import CONTROL_TOOLS, execute


@pytest.fixture(autouse=True)
def _isolate_current_task(tmp_path, monkeypatch):
    """控制工具测试不得读取或归档开发者正在执行的真实任务。"""
    monkeypatch.setattr(tasks, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tasks, "CURRENT_TASK_FILE", tmp_path / "current.md")
    monkeypatch.setattr(tasks, "ARCHIVE_DIR", tmp_path / "archive")


def test_control_tools_registered_and_executable():
    """task_done / need_user 注册名一致、execute 返回可识别 observation。"""
    assert {"task_done", "need_user"} == CONTROL_TOOLS
    assert "任务完成信号" in execute("task_done", {"summary": "搞定"})
    assert "需要用户拍板" in execute("need_user", {"question": "A 还是 B"})
    assert execute("不是控制工具", {}) is None  # 派发链契约：外来名返回 None

def test_control_tools_auto_discovered():
    """工具自动发现：task_done/need_user 进了注册表，模型看得到。"""
    from src.tools import get_tools
    names = {t["function"]["name"] for t in get_tools()}
    assert {
        "task_done",
        "need_user",
        "psyche_catalog",
        "activate_skill",
        "adopt_asset",
        "feedback_asset",
    } <= names


def test_agent_psyche_load_requires_cognitive_gap():
    assert "必须说明" in execute("load_psyche", {"name": "testing"})
    assert "reason" in execute(
        "load_psyche",
        {"name": "testing", "reason": "当前测试只覆盖实现细节，没有锁住失败类"},
    )


def test_psyche_catalog_is_query_only():
    out = execute("psyche_catalog", {"query": "warrant"})
    assert "paper-deep-read" in out
    assert "自动加载" in out

def _tc(name, args_json):
    return {"id": name, "type": "function",
            "function": {"name": name, "arguments": args_json}}

def test_detect_control_signal_extracts_name_and_payload():
    assert llm._detect_control_signal([_tc("need_user", '{"question": "选哪个?"}')]) == (
        "need_user", "选哪个?")
    assert llm._detect_control_signal([_tc("task_done", '{"summary": "完成"}')]) == (
        "task_done", "完成")
    assert llm._detect_control_signal([_tc("read_file", '{"path": "x"}')]) is None


def test_task_done_signal_requires_accepted_tool_result():
    call = _tc("task_done", '{"summary": "完成"}')
    ctx = CacheContext(log_msgs=[
        {
            "role": "tool",
            "tool_call_id": "task_done",
            "content": "❌ task_done 被验收合同拦截",
        },
    ])
    assert llm._detect_control_signal([call], ctx) is None

    ctx.log[-1]["content"] = "[任务完成信号] 完成"
    assert llm._detect_control_signal([call], ctx) == ("task_done", "完成")


def test_run_conversation_sets_signal_and_ends(monkeypatch):
    """模型调 need_user → run_conversation 置 ctx.control_signal 并结束本轮（不再循环）。"""
    ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])
    calls = {"n": 0}

    def fake_eager(backend, messages, on_token, session_id, **kw):
        calls["n"] += 1
        # 第一轮就吐一个 need_user 工具调用
        return None, [_tc("need_user", '{"question": "继续吗?"}')], {}

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    out = llm.run_conversation(ctx, "干个活", on_token=lambda _t: None,
                               on_tool=lambda *_a: None)
    assert ctx.control_signal == "need_user"
    assert ctx.control_payload == "继续吗?"
    assert calls["n"] == 1, "拿到停止信号后不应再发请求"
    assert isinstance(out, str)

def test_run_conversation_resets_signal_each_turn(monkeypatch):
    """上一轮的信号不泄漏：新一轮开头复位，纯文本结束时 control_signal 仍为 None。"""
    ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])
    ctx.control_signal = "need_user"  # 模拟上一轮残留

    def fake_eager(backend, messages, on_token, session_id, **kw):
        return "答完了", None, {}  # 无工具调用 = 自然结束

    monkeypatch.setattr(llm, "_invoke_llm_eager", fake_eager)
    llm.run_conversation(ctx, "聊个天", on_token=lambda _t: None, on_tool=lambda *_a: None)
    assert ctx.control_signal is None, "本轮没调控制工具，信号应被复位为 None"


def test_task_done_auto_deactivates_task_psyches(monkeypatch):
    """task_done 收尾 task scope，但保留 general base。"""
    from src.psyche_bridge import inject_psyche, loaded_psyches_in_log

    ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])
    inject_psyche(ctx, "reversibility-awareness", source="agent", reason="当前操作需要可逆性判断")

    monkeypatch.setattr(
        llm,
        "_invoke_llm_eager",
        lambda *_a, **_k: (None, [_tc("task_done", '{"summary": "完成"}')], {}),
    )

    llm.run_conversation(ctx, "完成任务", on_token=lambda _t: None, on_tool=lambda *_a: None)

    active = [meta.get("id") or meta["name"] for meta in loaded_psyches_in_log(ctx)]
    assert active == ["general"]
    assert any(
        m.get("_psyche_event", {}).get("type") == "deactivate"
        and m["_psyche_event"]["id"] == "reversibility-awareness"
        for m in ctx.log
    )
