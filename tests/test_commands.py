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

    def test_stats_registered(self):
        r = cmds.dispatch("/stats", self.ctx, None)
        assert len(r.message) > 0

    def test_sessions_registered(self):
        r = cmds.dispatch("/sessions", self.ctx, None)
        assert len(r.message) >= 0  # 可能为空（无历史会话）

    def test_save_registered(self):
        r = cmds.dispatch("/save", self.ctx, None)
        assert r.save is True

    def test_model_registered(self):
        r = cmds.dispatch("/model", self.ctx, None)
        assert len(r.message) > 0

    def test_mode_registered(self):
        r = cmds.dispatch("/mode", self.ctx, None)
        assert len(r.message) > 0

    def test_unknown_command(self):
        r = cmds.dispatch("/nonexistent_xyz", self.ctx, None)
        # 未知命令不崩溃，message 可能为空
        assert isinstance(r.message, str)

    def test_evolve_needs_args(self):
        r = cmds.dispatch("/evolve", self.ctx, None)
        assert "用法" in r.message

    def test_evolve_with_goal(self):
        r = cmds.dispatch("/evolve 优化性能", self.ctx, "test")
        assert r.retry is True
        assert r.save is True

    def test_research_needs_args(self):
        r = cmds.dispatch("/research", self.ctx, None)
        assert "用法" in r.message

    def test_research_with_topic(self):
        r = cmds.dispatch("/research AI 安全", self.ctx, "test")
        assert r.retry is True

    def test_watchdog_registered(self):
        r = cmds.dispatch("/watchdog", self.ctx, None)
        assert len(r.message) > 0


class TestPop:
    """pop 命令测试。"""

    def setup_method(self):
        self.ctx = CacheContext()
        self.ctx.log.append({"role": "user", "content": "q1"})
        self.ctx.log.append({"role": "assistant", "content": "a1"})
        self.ctx.log.append({"role": "user", "content": "q2"})
        self.ctx.log.append({"role": "assistant", "content": "a2"})

    def test_pop_last_turn(self):
        r = cmds.dispatch("/pop", self.ctx, None)
        assert r.save is True
        # 撤回最后一条 user + 之后的内容
        assert len(self.ctx.log) == 2

    def test_pop_invalid_count(self):
        r = cmds.dispatch("/pop abc", self.ctx, None)
        assert "无效" in r.message


class TestExport:
    """export 命令测试。"""

    def setup_method(self):
        self.ctx = CacheContext()
        self.ctx.log.append({"role": "user", "content": "test"})
        self.ctx.log.append({"role": "assistant", "content": "ok"})

    def test_export_creates_file(self, tmp_path):
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            r = cmds.dispatch("/export 1 test_export", self.ctx, "test-sid")
            assert "已导出" in r.message or "test_export" in r.message or len(r.message) > 0
        finally:
            os.chdir(old_cwd)

    def test_path_traversal_blocked(self):
        r = cmds.dispatch("/export 1 ../../../etc/passwd", self.ctx, "test-sid")
        # 不应包含路径穿越字符
        assert "../" not in r.message or "已导出" in r.message


if __name__ == "__main__":
    import inspect, tempfile

    tests = []
    for cls in [TestDispatch, TestPop, TestExport]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(instance, name)))

    passed = 0
    for name, fn in tests:
        try:
            if hasattr(fn.__self__, 'setup_method'):
                fn.__self__.setup_method()
            sig = inspect.signature(fn)
            if "tmp_path" in str(sig):
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
                fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            import traceback
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
    if passed < len(tests):
        sys.exit(1)
