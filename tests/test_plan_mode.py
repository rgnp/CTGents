"""Plan Mode 测试 — set_plan_mode / is_plan_mode / get_tools / 命令分发。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cache_context import CacheContext
from src.commands import dispatch
from src.tools import _PLAN_BLOCKED, get_tools, is_plan_mode, set_plan_mode


class TestPlanModeTools:
    """Plan Mode 下工具过滤逻辑。"""

    def setup_method(self):
        set_plan_mode(False)

    def test_normal_mode_all_tools(self):
        tools = get_tools()
        assert len(tools) == 50, f"Expected 50 tools, got {len(tools)}"
        names = {t["function"]["name"] for t in tools}
        assert "write_file" in names
        assert "edit_file_lines" in names
        assert "delete_file" in names

    def test_plan_mode_filters_write_tools(self):
        set_plan_mode(True)
        tools = get_tools()
        assert len(tools) == 40, f"Expected 40 tools, got {len(tools)}"
        names = {t["function"]["name"] for t in tools}
        assert "write_file" not in names
        assert "edit_file_lines" not in names
        assert "delete_file" not in names
        assert "git_commit" not in names
        assert "remember" not in names
        assert "forget" not in names

    def test_plan_mode_preserves_read_tools(self):
        set_plan_mode(True)
        names = {t["function"]["name"] for t in get_tools()}
        for read_tool in ("read_file", "grep_code", "think", "search_web"):
            assert read_tool in names, f"Read tool {read_tool} should not be blocked"

    def test_restore_after_plan_mode(self):
        set_plan_mode(True)
        assert len(get_tools()) == 40
        set_plan_mode(False)
        assert len(get_tools()) == 50

    def test_is_plan_mode_flag(self):
        assert not is_plan_mode()
        set_plan_mode(True)
        assert is_plan_mode()
        set_plan_mode(False)
        assert not is_plan_mode()

    def test_all_blocked_tools_are_valid(self):
        """_PLAN_BLOCKED 中的每个工具名必须存在于正常工具列表中。"""
        normal_names = {t["function"]["name"] for t in get_tools()}
        for name in _PLAN_BLOCKED:
            assert name in normal_names, f"{name} in _PLAN_BLOCKED but not in get_tools()"

    def test_cache_invalidated_on_plan_toggle(self):
        tools1 = get_tools()
        set_plan_mode(True)
        tools2 = get_tools()
        assert tools1 is not tools2  # Cache should be different objects

    def test_tool_cache_stable_without_toggle(self):
        tools1 = get_tools()
        tools2 = get_tools()
        tools3 = get_tools()
        assert tools1 is tools2 is tools3  # Same cached object


class TestPlanCommand:
    """/plan 命令测试。"""

    def setup_method(self):
        set_plan_mode(False)
        self.ctx = CacheContext()

    def test_plan_enter(self):
        r = dispatch("/plan", self.ctx, None)
        assert "已激活" in r.message
        assert "禁用" in r.message
        assert is_plan_mode()

    def test_plan_exit(self):
        set_plan_mode(True)
        r = dispatch("/plan", self.ctx, None)
        assert "已退出" in r.message
        assert not is_plan_mode()

    def test_plan_toggle_twice(self):
        r1 = dispatch("/plan", self.ctx, None)
        assert is_plan_mode()
        r2 = dispatch("/plan", self.ctx, None)
        assert not is_plan_mode()
        assert "已激活" in r1.message
        assert "已退出" in r2.message
