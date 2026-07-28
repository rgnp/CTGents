"""commands.py 关键路径测试 — 命令分发、返回结果、边界条件。"""

import pytest

import src.commands as cmds
from src.cache_context import CacheContext

pytestmark = pytest.mark.slow

class TestDispatch:
    """dispatch() 测试。"""

    def setup_method(self):
        self.ctx = CacheContext()

    def test_empty_input_returns_empty(self):
        r = cmds.dispatch("", self.ctx, None)
        assert r.message == ""
        assert not r.exit
        assert not r.retry

    def test_help_registered(self):
        r = cmds.dispatch("/help", self.ctx, None)
        assert "指令列表" in r.message or len(r.message) > 0

    def test_help_shortcut(self):
        r = cmds.dispatch("/h", self.ctx, None)
        assert "指令列表" in r.message or len(r.message) > 0

    def test_clear_sets_save(self):
        r = cmds.dispatch("/clear", self.ctx, None)
        assert r.save is True
        assert len(self.ctx.log) == 0

    def test_exit_sets_exit(self):
        r = cmds.dispatch("/exit", self.ctx, None)
        assert r.exit is True

    def test_quit_alias(self):
        r = cmds.dispatch("/q", self.ctx, None)
        assert r.exit is True

    def test_new_save_and_clear(self):
        r = cmds.dispatch("/new", self.ctx, None)
        assert r.save is True
        assert r.clear is True

    def test_context_registered(self):
        r = cmds.dispatch("/context", self.ctx, "test-session")
        assert "Token" in r.message or len(r.message) > 0

    def test_sessions_registered(self):
        r = cmds.dispatch("/sessions", self.ctx, None)
        assert len(r.message) >= 0
        assert len(r.message) > 0

    def test_model_registered(self):
        r = cmds.dispatch("/model", self.ctx, None)
        assert len(r.message) > 0

    def test_unknown_command(self):
        r = cmds.dispatch("/nonexistent_xyz", self.ctx, None)
        assert isinstance(r.message, str)

    def test_compact_empty_noop(self):
        r = cmds.dispatch("/compact", self.ctx, None)
        assert "无可压缩" in r.message
        assert r.save is False

    def test_compact_forces_below_threshold(self, monkeypatch):
        """对话远未到 65% 也能手动压缩（force=True 绕过门槛）。"""
        import src.llm as llm
        monkeypatch.setattr(llm, "_make_brief_summary", lambda msgs, max_len=500, previous_summary=None: "摘要")
        log = []
        for i in range(12):
            log.append({"role": "user", "content": f"问题{i} " + "x" * 50})
            log.append({"role": "assistant", "content": f"回答{i} " + "y" * 50})
        ctx = CacheContext(log_msgs=log)
        before = len(ctx.log)
        r = cmds.dispatch("/compact", ctx, "s")
        assert r.save is True
        assert "已压缩" in r.message
        assert len(ctx.log) < before
        assert any("归档" in (m.get("content") or "") for m in ctx.log)

    def test_gap_without_action_lists_ledger(self, monkeypatch):
        import src.gaps as gaps

        monkeypatch.setattr(gaps, "format_gap_ledger", lambda: "gap ledger")
        r = cmds.dispatch("/gap", self.ctx, None)
        assert r.message == "gap ledger"

    def test_gap_accept_updates_lifecycle(self, monkeypatch):
        import src.gaps as gaps

        calls = []
        monkeypatch.setattr(
            gaps,
            "set_gap_status",
            lambda reference, status, note: calls.append((reference, status, note)) or "✅ accepted",
        )
        r = cmds.dispatch("/gap accept abc123 值得修", self.ctx, None)
        assert calls == [("abc123", "accepted", "值得修")]
        assert r.save is True

    def test_gap_verify_uses_detector_recheck(self, monkeypatch):
        import src.gaps as gaps

        monkeypatch.setattr(gaps, "verify_gap", lambda reference: f"verified {reference}")
        r = cmds.dispatch("/gap verify abc123", self.ctx, None)
        assert r.message == "verified abc123"

    def test_heartbeat_delivery_disposition_routes_to_shared_receipt(self, monkeypatch):
        import src.work_receipts as receipts

        calls = []
        monkeypatch.setattr(
            receipts,
            "resolve_latest_delivery",
            lambda action, note: calls.append((action, note)) or "✅ 已接受",
        )

        r = cmds.dispatch("/heartbeat accept 证据充分", self.ctx, None)

        assert calls == [("accept", "证据充分")]
        assert r.save is True

    def test_heartbeat_unknown_action_fails_with_usage(self):
        r = cmds.dispatch("/heartbeat approve", self.ctx, None)
        assert "用法" in r.message

    def test_fix_accepts_gap_and_retries_with_prompt(self, monkeypatch):
        import src.gaps as gaps
        from src.gaps import Gap, GapReport

        gap = Gap(
            source="static",
            gap_type="dead_code",
            severity="high",
            detail="unused",
            affected_files=["src/a.py"],
        )
        accepted = []
        monkeypatch.setattr(gaps, "get_last_report", lambda: GapReport(gaps=[gap]))
        monkeypatch.setattr(gaps, "get_gap_by_index", lambda _index: gap)
        monkeypatch.setattr(
            gaps,
            "set_gap_status",
            lambda reference, status, note: accepted.append((reference, status, note)) or "✅ accepted",
        )
        monkeypatch.setattr(gaps, "_make_fix_prompt", lambda _gap, _index: "fix this")

        r = cmds.dispatch("/fix 1", self.ctx, None)

        assert accepted == [(gap.id, "accepted", "通过 /fix 1 接受")]
        assert r.retry is True
        assert r.save is True
        assert self.ctx.log[-1] == {"role": "user", "content": "fix this"}
        assert gap.id in r.message
