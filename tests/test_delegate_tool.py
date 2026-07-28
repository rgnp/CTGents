"""delegate 工具本体——隔离、闸重试、control_signal、路径校验。

worker 循环用 fake run_conversation 替身（模块级哨兵确保零真 API 调用）；
断言的重心是隔离契约：worker 拿到的 kwargs（track_stats/session_id/tools/max_requests）、
全新 ctx、tracker 会话指针 finally 恢复。
"""

import json

import pytest

import src.llm as llm_mod
import src.tools.delegate as delegate_mod
from src import tracker
from src.cache_context import CacheContext
from src.params import DELEGATE
from src.tools.delegate import delegate


@pytest.fixture(autouse=True)
def _sentinel_no_real_llm(monkeypatch, tmp_path):
    """哨兵：任何漏网的真 LLM 调用直接炸；产出根指到 tmp，不碰真 knowledge/。"""
    import src.paths as paths

    def _boom(*_a, **_k):
        raise AssertionError("测试不允许真 LLM 调用")
    monkeypatch.setattr(llm_mod, "_invoke_llm_eager", _boom)
    monkeypatch.setattr(delegate_mod, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(delegate_mod, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(paths, "WORKSPACE_ROOT", tmp_path)
    llm_mod.clear_interrupt()
    yield
    llm_mod.clear_interrupt()


def _tc(name: str, args: dict) -> dict:
    return {"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


_URL = "https://arxiv.org/abs/2401.12345"
_GOOD_OUTPUT = ("调研正文。" * 60) + f"\n[已核] 论文提出 XYZ（{_URL}）"


def _make_fake_worker(tmp_path, output_rel="knowledge/test/report.md",
                      output_text=_GOOD_OUTPUT, report=f"结论：XYZ 可行，见 {_URL}"):
    """Fake run_conversation：模拟 worker 搜索+读页+写盘，返回报告。记录每次调用。"""
    calls = []

    def fake_run(ctx, prompt, **kwargs):
        calls.append({"ctx": ctx, "prompt": prompt, **kwargs})
        ctx.log.append({"role": "user", "content": prompt})
        ctx.log.append({"role": "assistant", "tool_calls": [
            _tc("search_web", {"query": "XYZ"}),
            _tc("read_page", {"url": _URL}),
        ]})
        ctx.log.append({"role": "tool", "content": f"1. Paper\n   [arxiv.org] {_URL}"})
        out = tmp_path / output_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output_text, encoding="utf-8")
        ctx.log.append({"role": "assistant", "content": report})
        return report

    return fake_run, calls


class TestHappyPath:
    def test_returns_gate_pass_path_and_conclusion(self, monkeypatch, tmp_path):
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        result = delegate("调研 XYZ", "knowledge/test/report.md")
        assert "✅ 出处闸: 通过" in result
        assert "knowledge/test/report.md" in result
        assert "read_file" in result          # referent 交接提示
        assert "XYZ 可行" in result           # worker 结论
        assert len(calls) == 1                # 过闸即不重试

    def test_isolation_contract(self, monkeypatch, tmp_path):
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        delegate("调研 XYZ", "knowledge/test/report.md")
        kw = calls[0]
        assert kw["track_stats"] is False
        assert kw["session_id"] == ""
        assert kw["max_requests"] == DELEGATE.worker_max_requests
        assert isinstance(kw["ctx"], CacheContext)
        tool_names = {t["function"]["name"] for t in kw["tools"]}
        assert "delegate" not in tool_names   # 防递归
        assert "need_user" not in tool_names  # 无人值守，不给 control 工具
        assert "search_web" in tool_names
        # worker 前缀带硬规则与产出路径
        prefix_text = kw["ctx"].prefix[0]["content"]
        assert "knowledge/test/report.md" in prefix_text
        assert "[已核]" in prefix_text

    def test_psyche_core_appended_to_prefix(self, monkeypatch, tmp_path):
        core = tmp_path / "core.md"
        core.write_text("# 领域核心\n> 版本: 0.1\n五个筛子……", encoding="utf-8")
        monkeypatch.setattr(
            "src.psyche_bridge._find_core_file",
            lambda name: str(core) if name == "autonomous-driving" else None,
        )
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        delegate("调研", "knowledge/test/report.md", psyche="autonomous-driving")
        prefix = calls[0]["ctx"].prefix
        assert len(prefix) == 2
        assert "五个筛子" in prefix[1]["content"]


class TestTrackerRestore:
    def test_restored_after_success(self, monkeypatch, tmp_path):
        fake, _ = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        tracker.set_session("main-sid")
        try:
            delegate("调研", "knowledge/test/report.md")
            assert tracker.current_session() == "main-sid"
        finally:
            tracker.set_session("")

    def test_restored_after_worker_crash(self, monkeypatch, tmp_path):
        def crash(ctx, prompt, **kwargs):
            tracker.set_session("")   # 模拟 run_conversation 入场覆盖
            raise RuntimeError("worker 崩了")
        monkeypatch.setattr(llm_mod, "run_conversation", crash)
        tracker.set_session("main-sid")
        try:
            with pytest.raises(RuntimeError):
                delegate("调研", "knowledge/test/report.md")
            assert tracker.current_session() == "main-sid"
        finally:
            tracker.set_session("")


class TestGateRetry:
    def test_retry_feedback_then_pass(self, monkeypatch, tmp_path):
        """第一轮产出编造 URL → 打回（输入含 ⛔）→ 第二轮修好 → 通过。"""
        bad = ("凑字数。" * 60) + "\n来源: https://fake.example.com/invented"
        calls = []

        def fake_run(ctx, prompt, **kwargs):
            calls.append(prompt)
            out = tmp_path / "knowledge/test/report.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            ctx.log.append({"role": "assistant", "tool_calls": [
                _tc("read_page", {"url": _URL})]})
            ctx.log.append({"role": "tool", "content": f"正文 {_URL}"})
            if len(calls) == 1:
                out.write_text(bad, encoding="utf-8")
                return "初版结论"
            out.write_text(_GOOD_OUTPUT, encoding="utf-8")
            return f"修正后结论 {_URL}"

        monkeypatch.setattr(llm_mod, "run_conversation", fake_run)
        result = delegate("调研", "knowledge/test/report.md")
        assert len(calls) == 2
        assert calls[1].startswith("⛔ 出处闸未通过")
        assert "fake.example.com" in calls[1]
        assert "✅ 出处闸: 通过" in result

    def test_fail_closed_after_retries(self, monkeypatch, tmp_path):
        """重试后仍不过 → 如实返回未通过 + 问题清单，绝不静默放行。"""
        bad = ("凑字数。" * 60) + "\n来源: https://fake.example.com/invented"
        fake, calls = _make_fake_worker(tmp_path, output_text=bad, report="硬说完成")
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        result = delegate("调研", "knowledge/test/report.md")
        assert len(calls) == 1 + DELEGATE.gate_retries
        assert "⛔ 出处闸: 未通过" in result
        assert "fake.example.com" in result


class TestControlSignals:
    def test_interrupt_propagates_to_main(self, monkeypatch, tmp_path):
        def fake_run(ctx, prompt, **kwargs):
            ctx.control_signal = "interrupted"
            return "[⏹️ 已中断]"
        monkeypatch.setattr(llm_mod, "run_conversation", fake_run)
        result = delegate("调研", "knowledge/test/report.md")
        assert "⏹️" in result
        assert llm_mod.is_interrupt_requested()  # 传染回主轮

    def test_need_user_returns_blocked(self, monkeypatch, tmp_path):
        def fake_run(ctx, prompt, **kwargs):
            ctx.control_signal = "need_user"
            ctx.control_payload = "要选哪个数据集？"
            return ""
        monkeypatch.setattr(llm_mod, "run_conversation", fake_run)
        result = delegate("调研", "knowledge/test/report.md")
        assert result.startswith("⛔")
        assert "要选哪个数据集" in result


class TestPathValidation:
    @pytest.mark.parametrize("bad_path", ["../escape.md", "src/evil.py",
                                          "sessions/x.md", ".git/hook"])
    def test_rejected_paths(self, monkeypatch, tmp_path, bad_path):
        fake, calls = _make_fake_worker(tmp_path)
        monkeypatch.setattr(llm_mod, "run_conversation", fake)
        result = delegate("调研", bad_path)
        assert result.startswith("⛔ delegate 拒绝")
        assert "knowledge/" in result   # 教正道
        assert calls == []              # worker 根本没起

    def test_disabled_switch(self, monkeypatch, tmp_path):
        from src.params import DelegateParams
        monkeypatch.setattr(delegate_mod, "DELEGATE", DelegateParams(enabled=False))
        result = delegate("调研", "knowledge/test/report.md")
        assert "已禁用" in result


class TestNoTaskTail:
    """活动任务不能通过 send-time 挂尾进入 payload。"""

    def test_active_step_does_not_change_payload(self, monkeypatch):
        monkeypatch.setattr("src.tasks.read_current_active_step", lambda: "步骤3：写测试")
        ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "p"}])
        ctx.log.append({"role": "user", "content": "hi"})
        assert ctx.send() == [
            {"role": "system", "content": "p"},
            {"role": "user", "content": "hi"},
        ]
