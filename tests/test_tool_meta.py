"""工具元数据自洽验证 — _meta 完整性、派生集合一致性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.tools import get_tools, _auto_reload_module, _init_registry
from src.tools._tool_meta import (
    _BUILTIN_MODULES,
    _load_raw_tools,
    _derive,
    TOOL_LABELS,
    PARALLEL_SAFE,
    PLAN_BLOCKED,
    SKIP_COMPRESS_TOOLS,
    DEDUP_BLACKLIST,
    _META_ALIASES,
    _refresh_globals,
)


class TestMetaPresence:
    """验证所有工具都有 _meta。"""

    def test_every_registered_tool_has_meta(self):
        """每个注册工具词条都有 _meta 且至少含 label。"""
        raw = _load_raw_tools()
        names = set()
        for t in raw:
            name = t["function"]["name"]
            meta = t.get("_meta", {})
            assert "label" in meta, (
                f"{name} 缺少 _meta.label。请在该工具的 TOOLS_* 定义中添加 _meta。"
            )
            names.add(name)

        assert len(names) >= 40, f"预期至少 40 个工具，实际 {len(names)} 个"

    def test_no_stale_meta_entries(self):
        """没有注册表外的残留 _meta 条目。"""
        raw = _load_raw_tools()
        registered = {t["function"]["name"] for t in raw}
        alias_names = set(_META_ALIASES.keys())

        for derived_set, set_name in [
            (PARALLEL_SAFE, "PARALLEL_SAFE"),
            (PLAN_BLOCKED, "PLAN_BLOCKED"),
            (SKIP_COMPRESS_TOOLS, "SKIP_COMPRESS_TOOLS"),
            (DEDUP_BLACKLIST, "DEDUP_BLACKLIST"),
        ]:
            for name in derived_set:
                assert name in registered or name in alias_names, (
                    f"{name} 在 {set_name} 中，但不在任何 TOOLS_* 列表或别名中"
                )

    def test_no_dead_entries_in_derived_sets(self):
        """5 个已知死工具不应出现在任何派生集合中。"""
        dead = {"undo_edit", "install_plugin", "mcp_connect",
                "mcp_disconnect", "mcp_save_config"}
        for name in dead:
            assert name not in PARALLEL_SAFE
            assert name not in PLAN_BLOCKED
            assert name not in SKIP_COMPRESS_TOOLS
            assert name not in DEDUP_BLACKLIST
            assert name not in TOOL_LABELS, f"{name} 不应出现在 TOOL_LABELS"


class TestMetaStripping:
    """_meta 从 API 可见工具列表中剥离。"""

    def test_get_tools_no_meta_key(self):
        """get_tools() 返回的工具定义不含 _meta 键。"""
        tools = get_tools()
        for t in tools:
            assert "_meta" not in t, (
                f"{t['function']['name']} 的 _meta 未被剥离"
            )

    def test_get_tools_has_all_names(self):
        """剥离后工具名完整，数量正确。"""
        tools = get_tools()
        names = {t["function"]["name"] for t in tools}
        assert len(tools) == 41, f"预期 41 个工具，实际 {len(tools)}"
        assert "read_file" in names
        assert "write_file" in names
        assert "self" in names


class TestLabelCoverage:
    """TOOL_LABELS 覆盖所有工具。"""

    def test_all_tools_have_label(self):
        tools = get_tools()
        for t in tools:
            name = t["function"]["name"]
            assert name in TOOL_LABELS, (
                f"{name} 未在 TOOL_LABELS 中，检查 _meta.label"
            )
            assert TOOL_LABELS[name], f"{name} 的 label 为空"

    def test_read_file_lines_alias_label(self):
        assert "read_file_lines" in TOOL_LABELS
        assert TOOL_LABELS["read_file_lines"] == "读取文件"


class TestHotReloadPreservesMeta:
    """热加载后元数据正确刷新。"""

    def test_refresh_globals_preserves_counts(self):
        """_refresh_globals() 保持派生的 frozenset 大小不变。"""
        _refresh_globals()
        assert len(TOOL_LABELS) == 42  # 41 + read_file_lines
        assert len(PARALLEL_SAFE) == 21
        assert len(PLAN_BLOCKED) == 8
        assert len(SKIP_COMPRESS_TOOLS) == 2
        assert len(DEDUP_BLACKLIST) == 12

    def test_reload_tools_preserves_meta(self):
        """热加载所有工具后 _meta 派生正确。"""
        from src.tools import reload_tools
        reload_tools()
        # 重新获取
        labels, psafe, pblock, skip, dedup = _derive()
        assert len(labels) == 42
        assert len(psafe) == 21
        assert len(pblock) == 8
