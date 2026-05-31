"""Tests for goal-driven long task (/goal command)."""

import pytest
from src.goal import GoalRunner, GoalState, _consecutive_errors, _parse_action, _smart_reload


# ═══════════════════════════════════════════════════════════════
# GoalState
# ═══════════════════════════════════════════════════════════════

class TestGoalState:
    def test_empty_state(self):
        s = GoalState(description="测试目标")
        ctx = s.to_context()
        assert "测试目标" in ctx
        assert "已完成: 0 步" in ctx

    def test_with_plan(self):
        s = GoalState(description="A", plan=["步骤1", "步骤2", "步骤3"])
        s.current_step = "步骤1"
        ctx = s.to_context()
        assert "[→] 步骤1" in ctx
        assert "[ ] 步骤2" in ctx

    def test_with_done_steps(self):
        s = GoalState(description="A", plan=["步骤1", "步骤2"])
        s.mark_done("步骤1")
        s.mark_done("步骤1")
        ctx = s.to_context()
        assert "[✓] 步骤1" in ctx
        assert len(s.done) == 1

    def test_add_history_within_window(self):
        s = GoalState(description="A")
        s.add_history(1, "read_file(foo)", "content here")
        s.add_history(2, "write_file(bar)", "")
        assert len(s.history) == 2
        assert s.summary == ""

    def test_add_history_overflow_to_summary(self):
        s = GoalState(description="A")
        for i in range(1, 6):
            s.add_history(i, f"step_{i}", f"result_{i}")
        assert len(s.history) == 3
        assert "Step1" in s.summary
        assert "Step2" in s.summary

    def test_add_history_with_error(self):
        s = GoalState(description="A")
        s.add_history(1, "write_file(foo)", "", "PermissionError")
        assert s.history[0]["error"] == "PermissionError"

    def test_history_result_truncation(self):
        s = GoalState(description="A")
        big = "x" * 2000
        s.add_history(1, "read_file(foo)", big)
        assert len(s.history[0]["result"]) == 1000

    def test_add_history_within_window(self):
        s = GoalState(description="A")
        # 操作类工具（非 INFO_TOOLS）结果走 history
        s.add_history(1, "write_file(foo)", "write_file", "content here")
        s.add_history(2, "git_commit(init)", "git_commit", "")
        assert len(s.history) == 2
        assert s.summary == ""

    def test_add_history_overflow_to_summary(self):
        s = GoalState(description="A")
        for i in range(1, 9):  # WINDOW_SIZE=6，需要 7+ 才会溢出
            s.add_history(i, f"write_file(step_{i})", "write_file", f"result_{i}")
        assert len(s.history) == 6
        assert "Step1" in s.summary
        assert "Step2" in s.summary

    def test_add_history_with_error(self):
        s = GoalState(description="A")
        s.add_history(1, "write_file(foo)", "write_file", "", "PermissionError")
        assert s.history[0]["error"] == "PermissionError"

    def test_history_result_truncation(self):
        s = GoalState(description="A")
        big = "x" * 3000
        s.add_history(1, "write_file(foo)", "write_file", big)
        assert len(s.history[0]["result"]) == 2000

    def test_info_tool_goes_to_knowledge(self):
        s = GoalState(description="A")
        s.add_history(1, "read_file(foo.py)", "read_file", "file content here")
        assert "read_file(foo.py)" in s.knowledge
        assert s.knowledge["read_file(foo.py)"] == "file content here"
        # 信息类工具结果不在 history 的 result 中
        assert s.history[0]["result"] == ""

    def test_knowledge_same_key_overwrites(self):
        s = GoalState(description="A")
        s.add_history(1, "read_file(foo.py)", "read_file", "version1")
    def test_knowledge_fifo_eviction(self):
        s = GoalState(description="A")
        # 填满 knowledge（KNOWLEDGE_MAX_ENTRIES*2=20 以后开始淘汰）
        keys = ["read_file", "scan_project", "git_status", "list_files",
                "grep_code", "read_page", "count_lines", "check_project",
                "rag_query", "discover", "git_diff", "git_log",
                "git_branch", "git_status", "rag_status",
                "read_file_lines", "scan_project", "list_files",
                "read_page", "grep_code", "count_lines", "check_project"]
        for i, k in enumerate(keys[:22]):
            s.add_history(i, k, k.split("(")[0] if "(" not in k else k, f"data{i}")
        # 应该淘汰到 20 条以内
        assert len(s.knowledge) <= 20

    def test_to_context_shows_knowledge(self):
        s = GoalState(description="A")
        s.add_history(1, "read_file(foo.py)", "read_file", "重要的项目信息")
        ctx = s.to_context()
        assert "已获取的项目信息" in ctx
        assert "重要的项目信息" in ctx
        assert "知识条目: 1" in ctx

    def test_to_context_no_knowledge_no_section(self):
        s = GoalState(description="A")
        ctx = s.to_context()
        # 没有知识缓存时不显示该段落
        assert "已获取的项目信息" not in ctx or "知识条目: 0" in ctx

    def test_knowledge_fifo_eviction(self):
        s = GoalState(description="A")
        # 填满 knowledge（KNOWLEDGE_MAX_ENTRIES*2=20 以后开始淘汰）
        keys = ["read_file", "scan_project", "git_status", "list_files",
                "grep_code", "read_page", "count_lines", "check_project",
                "rag_query", "discover", "git_diff", "git_log",
                "git_branch", "git_status", "rag_status",
                "read_file_lines", "scan_project", "list_files",
                "read_page", "grep_code", "count_lines", "check_project"]
        for i, k in enumerate(keys[:22]):
            s.add_history(i, k, k.split("(")[0] if "(" not in k else k, f"data{i}")
        # 应该淘汰到 20 条以内
        assert len(s.knowledge) <= 20

    def test_mark_done_duplicate(self):
        s = GoalState(description="A")
        s.mark_done("step_a")
        s.mark_done("step_a")
        assert s.done == ["step_a"]
    def test_json_with_whitespace(self):
        result = _parse_action('  {"action": "tool_call"}  ')
        assert result["action"] == "tool_call"

    def test_markdown_code_block(self):
        assert _parse_action('```json\n{"action":"done"}\n```') == {"action": "done"}

    def test_markdown_no_lang(self):
        assert _parse_action('```\n{"action":"done"}\n```') == {"action": "done"}

    def test_nested_braces(self):
        result = _parse_action('{"action":"tool_call","args":{"path":"foo.py"}}')
        assert result["args"] == {"path": "foo.py"}

    def test_surrounded_by_text(self):
        result = _parse_action('好的。\n{"action":"done"}\n以上。')
        assert result == {"action": "done"}

    def test_none_input(self):
        assert _parse_action(None) is None

    def test_empty_string(self):
        assert _parse_action("") is None

    def test_invalid_json(self):
        assert _parse_action("not json at all") is None

    def test_only_brace_block(self):
        assert _parse_action('pre {"action":"done"} post') == {"action": "done"}


# ═══════════════════════════════════════════════════════════════
# _smart_reload
# ═══════════════════════════════════════════════════════════════

class TestSmartReload:
    def test_non_python_file(self):
        assert _smart_reload("README.md") is None

    def test_not_source_dir(self):
        assert _smart_reload("/tmp/test.py") is None

    def test_source_file_not_loaded(self):
        assert _smart_reload("src/nonexistent_xyz.py") is None

    def test_source_file_windows_path(self):
        assert _smart_reload("src\\nonexistent_xyz.py") is None


# ═══════════════════════════════════════════════════════════════
# GoalRunner._tool_call_to_action
# ═══════════════════════════════════════════════════════════════

class TestToolCallToAction:
    def test_basic_tool_call(self):
        r = GoalRunner("test")
        tc = [{"function": {"name": "read_file", "arguments": '{"path": "foo.py"}'}}]
        result = r._tool_call_to_action(tc, "思考")
        assert result["action"] == "tool_call"
        assert result["tool"] == "read_file"
        assert result["args"] == {"path": "foo.py"}

    def test_with_meta_json_in_content(self):
        r = GoalRunner("test")
        tc = [{"function": {"name": "write_file", "arguments": '{"path":"a.py"}'}}]
        content = '{"reasoning":"创建文件","plan":["s1"],"mark_done":"ok"}\n然后'
        result = r._tool_call_to_action(tc, content)
        assert result["tool"] == "write_file"
        assert result["reasoning"] == "创建文件"
        assert result["plan"] == ["s1"]
        assert result["mark_done"] == "ok"

    def test_empty_tool_calls(self):
        r = GoalRunner("test")
        assert r._tool_call_to_action(None, "") is None
        assert r._tool_call_to_action([], "") is None

    def test_invalid_arguments_json(self):
        r = GoalRunner("test")
        tc = [{"function": {"name": "read_file", "arguments": "not-json"}}]
        result = r._tool_call_to_action(tc, "")
        assert result["tool"] == "read_file"
        assert result["args"] == {}


# ═══════════════════════════════════════════════════════════════
# GoalRunner._build_messages — 回归测试
# ═══════════════════════════════════════════════════════════════

class TestBuildMessages:
    def test_returns_nonempty_list(self):
        r = GoalRunner("测试")
        msgs = r._build_messages()
        assert isinstance(msgs, list), f"expected list, got {type(msgs)}"
        assert len(msgs) >= 2
        for m in msgs:
            assert isinstance(m, dict)
            assert m["role"] in ("system", "user")
            assert isinstance(m["content"], str)
            assert m["content"], "empty content"

    def test_system_contains_goal_description(self):
        r = GoalRunner("实现HTTP服务器")
        msgs = r._build_messages()
        assert "实现HTTP服务器" in msgs[0]["content"]
