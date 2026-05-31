"""Storm 去重模块测试。"""

import json
import pytest
from src.tools.storm import (
    storm_check, storm_record, reset_storm, get_storm_stats, get_blacklist,
)


class TestStormDedup:
    """Storm 去重核心逻辑测试。"""

    def setup_method(self):
        reset_storm()

    def test_first_call_not_duplicate(self):
        """第一次调用同一工具不应标记为重复。"""
        result = storm_check("read_file", {"path": "main.py"})
        assert result is None

    def test_second_call_same_args_is_duplicate(self):
        """相同工具 + 相同参数，第二次调用应标记为重复。"""
        storm_check("read_file", {"path": "main.py"})
        result = storm_check("read_file", {"path": "main.py"})
        assert result is not None
        assert "⚡重复调用" in result
        assert "read_file" in result

    def test_same_tool_different_args_not_duplicate(self):
        """相同工具但不同参数不应标记为重复。"""
        storm_check("read_file", {"path": "main.py"})
        result = storm_check("read_file", {"path": "utils.py"})
        assert result is None

    def test_different_tool_same_args_not_duplicate(self):
        """不同工具但相同参数不应标记为重复。"""
        storm_check("read_file", {"path": "main.py"})
        result = storm_check("grep_code", {"path": "main.py"})
        assert result is None

    def test_blacklist_tools_not_tracked(self):
        """黑名单中的工具不应被追踪或去重。"""
        reset_storm()
        # 第一次调用黑名单工具
        r1 = storm_check("write_file", {"path": "test.txt", "content": "hello"})
        assert r1 is None
        # 第二次相同调用，应该仍为 None（不追踪）
        r2 = storm_check("write_file", {"path": "test.txt", "content": "hello"})
        assert r2 is None

    def test_blacklist_not_in_window(self):
        """黑名单工具不应占用窗口位置。"""
        reset_storm()
        storm_check("write_file", {"path": "a.txt"})
        storm_check("write_file", {"path": "b.txt"})
        storm_check("write_file", {"path": "c.txt"})
        # 窗口应该还是空的
        assert get_storm_stats()["window_size"] == 0

    def test_window_size_limit(self):
        """窗口超过限制时自动淘汰旧记录。"""
        reset_storm()
        # 填充窗口到上限 + 1
        for i in range(9):
            r = storm_check("read_file", {"path": f"file_{i}.py"})
            assert r is None, f"第 {i+1} 次调用不应重复"

        assert get_storm_stats()["window_size"] == 8, "窗口应保持最多 8 条"

        # 此时 file_0.py 已被淘汰，可以再次调用
        r = storm_check("read_file", {"path": "file_0.py"})
        assert r is None, "最早的一条已被淘汰，应可再次调用"

    def test_args_normalization_none_values(self):
        """None 值参数不应影响哈希。"""
        reset_storm()
        r1 = storm_check("read_file", {"path": "main.py", "start_line": None})
        r2 = storm_check("read_file", {"path": "main.py"})
        assert r2 is not None, "None 值被忽略后，两条调用应匹配为重复"

    def test_args_normalization_key_order(self):
        """参数键顺序不影响去重判断。"""
        reset_storm()
        r1 = storm_check("read_file", {"path": "main.py", "encoding": "utf-8"})
        r2 = storm_check("read_file", {"encoding": "utf-8", "path": "main.py"})
        assert r2 is not None, "顺序不同但内容相同应标记为重复"

    def test_mixed_normal_and_blacklist(self):
        """正常工具去重不受黑名单工具调用影响。"""
        reset_storm()
        # 先调黑名单
        storm_check("run_command", {"command": "ls"})
        # 再调正常工具
        r1 = storm_check("read_file", {"path": "main.py"})
        assert r1 is None
        r2 = storm_check("read_file", {"path": "main.py"})
        assert r2 is not None

    def test_reset_clears_window(self):
        """reset 后窗口清空，之前的记录不再视为重复。"""
        storm_check("read_file", {"path": "main.py"})
        storm_check("read_file", {"path": "main.py"})

        reset_storm()
        assert get_storm_stats()["window_size"] == 0

        r = storm_check("read_file", {"path": "main.py"})
        assert r is None, "reset 后相同调用不应视为重复"

    def test_return_format(self):
        """重复标记的格式应包含工具名和提示。"""
        storm_check("grep_code", {"pattern": "def main"})
        result = storm_check("grep_code", {"pattern": "def main"})
        assert result is not None
        assert isinstance(result, str)
        assert "grep_code" in result
        assert "⚡" in result

    def test_repeated_third_call_still_duplicate(self):
        """第三次重复调用仍应标记为重复（直到窗口淘汰）。"""
        storm_check("read_file", {"path": "main.py"})
        storm_check("read_file", {"path": "main.py"})  # 第二次 → 重复
        r3 = storm_check("read_file", {"path": "main.py"})  # 第三次
        assert r3 is not None, "第三次仍未翻篇，应继续标记重复"

    def test_think_is_blacklisted(self):
        """think 工具在黑名单中，不应去重。"""
        storm_check("think", {"thought": "思考1"})
        r2 = storm_check("think", {"thought": "思考1"})
        assert r2 is None, "think 在黑名单中，不应去重"

    def test_mcp_tools_not_blacklisted(self):
        """MCP 工具不在黑名单中，应执行去重（如 mcp_read）。"""
        reset_storm()
        r1 = storm_check("mcp_read", {"path": "/tmp/test.txt"})
        assert r1 is None
        r2 = storm_check("mcp_read", {"path": "/tmp/test.txt"})
        assert r2 is not None, "MCP 读取工具应被去重"

    def test_plugin_tools_not_blacklisted(self):
        """插件工具不在黑名单中，应执行去重。"""
        reset_storm()
        r1 = storm_check("my_plugin_query", {"q": "hello"})
        assert r1 is None
        r2 = storm_check("my_plugin_query", {"q": "hello"})
        assert r2 is not None, "插件读取工具应被去重"


class TestStormBlacklist:
    """黑名单完整性测试。"""

    def test_blacklist_contains_all_write_tools(self):
        bl = get_blacklist()
        for tool in ("write_file", "edit_file_lines", "undo_edit", "delete_file"):
            assert tool in bl, f"{tool} 应在黑名单中"

    def test_blacklist_contains_git_mutation_tools(self):
        bl = get_blacklist()
        for tool in ("git_commit", "git_push", "git_pr"):
            assert tool in bl, f"{tool} 应在黑名单中"

    def test_blacklist_contains_memory_tools(self):
        bl = get_blacklist()
        for tool in ("remember", "forget"):
            assert tool in bl, f"{tool} 应在黑名单中"

    def test_blacklist_contains_install_plugin(self):
        assert "install_plugin" in get_blacklist()

    def test_blacklist_contains_mcp_connect_disconnect(self):
        bl = get_blacklist()
        assert "mcp_connect" in bl
        assert "mcp_disconnect" in bl

    def test_common_read_tools_not_in_blacklist(self):
        bl = get_blacklist()
        for tool in ("read_file", "search_web", "grep_code", "rag_query",
                     "git_status", "git_log", "git_diff", "git_branch"):
            assert tool not in bl, f"{tool} 不应在黑名单中"



class TestStormCache:
    """Storm 结果缓存测试。"""

    def setup_method(self):
        reset_storm()

    def test_record_then_cache_hit_returns_actual_result(self):
        """storm_record 缓存结果后，去重命中返回实际结果而非静态字符串。"""
        storm_check("read_file", {"path": "main.py"})  # 首次，通过
        storm_record("read_file", {"path": "main.py"}, "content of main.py")

        result = storm_check("read_file", {"path": "main.py"})  # 去重命中
        assert result is not None
        assert "content of main.py" in result
        assert "已缓存" in result

    def test_pending_when_no_cache(self):
        """未调用 storm_record 时，去重命中返回等待标记（非缓存结果）。"""
        storm_check("read_file", {"path": "main.py"})  # 首次，通过（但未执行/未 record）

        result = storm_check("read_file", {"path": "main.py"})  # 去重命中，无缓存
        assert result is not None
        assert "⚡重复调用" in result
        assert "正在执行中" in result

    def test_cache_evicted_with_window(self):
        """窗口淘汰旧记录时同步清理缓存。"""
        # 填充窗口到上限（8 条）
        for i in range(8):
            storm_check("read_file", {"path": f"file_{i}.py"})
            storm_record("read_file", {"path": f"file_{i}.py"}, f"content_{i}")

        assert get_storm_stats()["cached"] == 8

        # 第 9 条挤掉 file_0
        storm_check("read_file", {"path": "file_8.py"})
        storm_record("read_file", {"path": "file_8.py"}, "content_8")

        assert get_storm_stats()["cached"] == 8  # 仍为 8，file_0 被淘汰

        # file_0 被淘汰后重新调用应通过（非重复）
        r = storm_check("read_file", {"path": "file_0.py"})
        assert r is None, "被淘汰后应可再次调用"

    def test_record_blacklist_ignored(self):
        """storm_record 对黑名单工具是空操作。"""
        storm_record("write_file", {"path": "x.txt", "content": "x"}, "ok")
        # 不应缓存，也不应报错
        assert get_storm_stats()["cached"] == 0

    def test_reset_clears_cache(self):
        """reset_storm 清空窗口和缓存。"""
        storm_check("read_file", {"path": "main.py"})
        storm_record("read_file", {"path": "main.py"}, "content")
        assert get_storm_stats()["cached"] == 1

        reset_storm()
        assert get_storm_stats()["cached"] == 0
        assert get_storm_stats()["window_size"] == 0

    def test_cache_hit_stats_count(self):
        """缓存命中时 hits 计数正确递增。"""
        storm_check("read_file", {"path": "main.py"})
        storm_record("read_file", {"path": "main.py"}, "content")

        storm_check("read_file", {"path": "main.py"})  # 缓存命中
        assert get_storm_stats()["hits"] == 1

    def test_different_params_different_cache(self):
        """不同参数的调用有独立的缓存。"""
        storm_check("read_file", {"path": "a.py"})
        storm_record("read_file", {"path": "a.py"}, "content A")

        storm_check("read_file", {"path": "b.py"})
        storm_record("read_file", {"path": "b.py"}, "content B")

        # a 的缓存命中
        r1 = storm_check("read_file", {"path": "a.py"})
        assert "content A" in r1

        # b 的缓存命中
        r2 = storm_check("read_file", {"path": "b.py"})
        assert "content B" in r2