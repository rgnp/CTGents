"""测试 safety 模块：安全等级、模式切换、会话信任、安全检查。"""

import pytest
from src.safety import (
    SafetyLevel,
    TOOL_SAFETY,
    get_mode,
    set_mode,
    get_safety_level,
    check_tool,
    trust_tool,
    revoke_trust,
    is_trusted,
    list_trusted,
    clear_trust,
    get_mode_summary,
    format_tool_safety,
)


class TestSafetyLevelEnum:
    def test_values(self):
        assert SafetyLevel.SAFE.value == "safe"
        assert SafetyLevel.RISKY.value == "risky"
        assert SafetyLevel.DANGEROUS.value == "dangerous"

    def test_ordering(self):
        """安全等级枚举是可比较的。"""
        assert SafetyLevel.SAFE != SafetyLevel.RISKY
        assert SafetyLevel.DANGEROUS != SafetyLevel.SAFE


class TestToolSafetyRegistry:
    def test_safe_tools_are_read_only(self):
        """读操作工具应为 SAFE。"""
        safe_tools = ["search_web", "read_file", "read_file_lines",
                       "list_files", "grep_code", "git_status",
                       "git_diff", "git_log", "git_branch",
                       "scan_project", "check_project", "discover",
                       "think", "recall"]
        for tool in safe_tools:
            assert TOOL_SAFETY.get(tool) == SafetyLevel.SAFE, f"{tool} 应为 SAFE"

    def test_risky_tools_modify_files(self):
        """写操作工具应为 RISKY。"""
        risky_tools = ["write_file", "edit_file_lines", "undo_edit",
                        "delete_file", "run_python", "run_command",
                        "remember", "forget", "git_commit",
                        "install_plugin"]
        for tool in risky_tools:
            assert TOOL_SAFETY.get(tool) == SafetyLevel.RISKY, f"{tool} 应为 RISKY"

    def test_dangerous_tools_are_destructive(self):
        """破坏性操作为 DANGEROUS。"""
        dangerous_tools = ["git_push"]
        for tool in dangerous_tools:
            assert TOOL_SAFETY.get(tool) == SafetyLevel.DANGEROUS, f"{tool} 应为 DANGEROUS"

    def test_unknown_tool_defaults_to_risky(self):
        """未注册的工具默认为 RISKY。"""
        assert get_safety_level("imaginary_tool") == SafetyLevel.RISKY


class TestGetSafetyLevel:
    def test_known_tool(self):
        assert get_safety_level("read_file") == SafetyLevel.SAFE
        assert get_safety_level("write_file") == SafetyLevel.RISKY
        assert get_safety_level("git_push") == SafetyLevel.DANGEROUS

    def test_unknown_tool(self):
        assert get_safety_level("nonexistent_tool") == SafetyLevel.RISKY


class TestMode:
    def setup_method(self):
        set_mode("manual")

    def test_default_mode(self):
        """默认模式是 manual。"""
        assert get_mode() == "manual"

    def test_set_mode_manual(self):
        ok, msg = set_mode("manual")
        assert ok
        assert "manual" in msg
        assert get_mode() == "manual"

    def test_set_mode_auto(self):
        ok, msg = set_mode("auto")
        assert ok
        assert "auto" in msg
        assert get_mode() == "auto"

    def test_set_invalid_mode(self):
        ok, msg = set_mode("invalid")
    def test_set_invalid_mode(self):
        ok, msg = set_mode("manual")
        assert ok
        ok, msg = set_mode("invalid")
        assert not ok
        assert "无效" in msg
        assert get_mode() == "manual"  # 保持不变
    def test_mode_case_insensitive(self):
        ok, _ = set_mode("MANUAL")
        assert ok
        assert get_mode() == "manual"

    def test_mode_summary(self):
        # 安全模式已移除，返回空字符串
        summary = get_mode_summary()
        assert summary == ""

    def test_mode_summary_with_trust(self):
        # 安全模式已移除，返回空字符串
        summary = get_mode_summary()
        assert summary == ""


class TestSessionTrust:
    def setup_method(self):
        clear_trust()

    def test_trust_tool(self):
        msg = trust_tool("read_file")
        assert "信任" in msg
        assert is_trusted("read_file")

    def test_revoke_trust(self):
        trust_tool("write_file")
        assert is_trusted("write_file")
        revoke_trust("write_file")
        assert not is_trusted("write_file")

    def test_list_trusted_empty(self):
        result = list_trusted()
        assert "无信任工具" in result

    def test_list_trusted_with_items(self):
        trust_tool("read_file")
        trust_tool("git_push")
        result = list_trusted()
        assert "read_file" in result
        assert "git_push" in result

    def test_clear_trust(self):
        trust_tool("write_file")
        trust_tool("delete_file")
        clear_trust()
        result = list_trusted()
        assert "无信任工具" in result


class TestCheckTool:
    def setup_method(self):
        set_mode("manual")
        clear_trust()

    def test_safe_tool_always_allowed(self):
        """SAFE 工具在任何模式下都放行。"""
        assert check_tool("read_file") == "allow"
        assert check_tool("list_files") == "allow"

    def test_risky_in_manual_mode(self):
        """RISKY 工具在 manual 模式下需要确认。"""
        assert check_tool("write_file") == "confirm"

    def test_risky_in_auto_mode(self):
        """RISKY 工具在 auto 模式下放行。"""
        set_mode("auto")
        assert check_tool("write_file") == "allow"

    def test_dangerous_always_confirms(self):
        """DANGEROUS 工具始终需要确认，auto 模式也不例外。"""
        assert check_tool("git_push") == "confirm"
        set_mode("auto")
        assert check_tool("git_push") == "confirm"

    def test_trusted_tool_bypasses_check(self):
        """被信任的工具直接放行。"""
        trust_tool("git_push")
        assert check_tool("git_push") == "allow"

    def test_trusted_risky_in_manual(self):
        """RISKY 工具被信任后即使 manual 也放行。"""
        trust_tool("write_file")
        assert check_tool("write_file") == "allow"

    def test_unknown_tool_default_risky(self):
        """未注册工具以 RISKY 处理。"""
        assert check_tool("unknown_tool") == "confirm"
        set_mode("auto")
        assert check_tool("unknown_tool") == "allow"


class TestFormatToolSafety:
    def test_format_known(self):
        result = format_tool_safety("read_file")
        assert "read_file" in result
        assert "safe" in result

    def test_format_with_trust(self):
        trust_tool("git_push")
        result = format_tool_safety("git_push")
        assert "✅" in result
        assert "dangerous" in result
