"""工具元数据自洽验证 — _meta 完整性、派生集合一致性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools import get_tools
from src.tools._tool_meta import (
    _META_ALIASES,
    DEDUP_BLACKLIST,
    PARALLEL_SAFE,
    PLAN_BLOCKED,
    SKIP_COMPRESS_TOOLS,
    TOOL_LABELS,
    _derive,
    _load_raw_tools,
    _refresh_globals,
)


class TestMetaPresence:
    """验证所有工具都有 _meta。"""

    def test_every_registered_tool_has_meta(self):
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
        tools = get_tools()
        for t in tools:
            assert "_meta" not in t, (
                f"{t['function']['name']} 的 _meta 未被剥离"
            )

    def test_get_tools_has_all_names(self):
        tools = get_tools()
        names = {t["function"]["name"] for t in tools}
        assert len(tools) == 50, f"预期 50 个工具，实际 {len(tools)}"
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
        _refresh_globals()
        assert len(PARALLEL_SAFE) == 26
        assert len(PLAN_BLOCKED) == 10
        assert len(DEDUP_BLACKLIST) == 15

    def test_reload_tools_preserves_meta(self):
        from src.tools import reload_tools
        reload_tools()
        labels, psafe, pblock, skip, dedup = _derive()
        assert len(labels) == 51
        assert len(psafe) == 26
        assert len(pblock) == 10


class TestDispatchContract:
    """execute_tool 责任链契约：execute 对不归自己的名字必须返回 None。

    回归 691f0ac 引入的 bug：rag.execute 对未知名返回 "未知 RAG 工具: {name}"
    而非 None。因模块按字母序派发，rag 排在 research/self/think/web 之前，
    把这四家的工具全部截胡——self/think/search_web 等几十个工具静默失效。
    """

    def test_no_executor_claims_foreign_name(self):
        """喂一个保证不存在的工具名，每个模块的 execute 都必须返回 None。"""
        import src.config  # noqa: F401  触发 .env / 代理加载
        from src.tools import _EXECUTORS
        sentinel = "__definitely_not_a_real_tool_name__"
        for ex in _EXECUTORS:
            result = ex(sentinel, {})
            assert result is None, (
                f"{ex.__module__} 的 execute 截胡了外来工具名，返回 {result!r}；"
                f"不归自己的名字必须返回 None 以交还责任链"
            )

    def test_self_routes_to_self_module_not_rag(self):
        """路由 self 工具到 self 模块，而非被 rag 吞成错误串。"""
        import src.config  # noqa: F401
        from src.tools import execute_tool

        class _Fn:
            name = "self"
            arguments = '{"topic": "capabilities"}'

        class _Call:
            function = _Fn()

        out = execute_tool(_Call())
        assert "未知 RAG 工具" not in out, "self 仍被 rag 截胡"
        assert "未注册的工具" not in out, "self 未被任何模块认领"
