"""commands.py 关键路径测试 — 命令分发、返回结果、边界条件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.commands as cmds
from src.cache_context import CacheContext


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
        # 未知命令不崩溃，message 可能为空
        assert isinstance(r.message, str)

    def test_compact_empty_noop(self):
        r = cmds.dispatch("/compact", self.ctx, None)
        assert "无可压缩" in r.message
        assert r.save is False

    def test_compact_forces_below_threshold(self):
        """对话远未到 65% 也能手动压缩（force=True 绕过门槛）。"""
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

    def test_evolve_needs_args(self):
        r = cmds.dispatch("/evolve", self.ctx, None)
        assert "用法" in r.message

    def test_evolve_with_goal(self, tmp_path, monkeypatch):
        import src.evolution_runner as runner
        run_root = tmp_path / "evolution"
        monkeypatch.setattr(runner, "RUN_ROOT", run_root)
        monkeypatch.setattr(runner, "RUNS_DIR", run_root / "runs")
        monkeypatch.setattr(runner, "ACTIVE_RUN_FILE", run_root / "active.json")

        r = cmds.dispatch("/evolve 优化性能", self.ctx, "test")
        assert r.retry is True
        assert r.save is True
        assert "runner" in r.message
        assert runner.load_active_evolution_run() is not None


class TestGoalCommand:
    """/goal 指令:只收文本递给 main,缺标准给用法提示。"""

    def setup_method(self):
        from src.cache_context import CacheContext
        self.ctx = CacheContext()

    def test_goal_sets_field(self):
        import src.commands as cmds
        r = cmds.dispatch("/goal 写文档 || 含示例 | 100字以上", self.ctx, None)
        assert r.goal == "写文档 || 含示例 | 100字以上"
        assert not r.message

    def test_goal_without_criteria_shows_usage(self):
        import src.commands as cmds
        r = cmds.dispatch("/goal 只有目标", self.ctx, None)
        assert not r.goal
        assert "用法" in r.message and "标准" in r.message
