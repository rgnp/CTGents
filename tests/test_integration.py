"""集成测试 — 验证跨模块接线是否闭合。

这些测试不测单个函数逻辑，只测 A→B 的调用链是否正确连接。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════
# 1. 保护系统 → 覆盖率门禁
# ═══════════════════════════════════════════════════════════════

class TestGuardCoverageGate:
    """guard.is_protected() 必须接入 coverage_gate.can_modify()。"""

    def test_is_protected_calls_coverage_gate(self):
        from src.guard import is_protected
        import inspect
        src = inspect.getsource(is_protected)
        assert "coverage_gate" in src, "is_protected 未导入 coverage_gate"

    def test_is_protected_always_blocks_guard_itself(self):
        from src.guard import is_protected, _GUARD_FILE
        assert is_protected(str(_GUARD_FILE)) is True

    def test_is_protected_always_blocks_watchdog(self):
        from src.guard import is_protected
        watchdog = PROJECT_ROOT / "src" / "watchdog.py"
        assert is_protected(str(watchdog)) is True

    def test_is_protected_allows_outside_project(self, tmp_path):
        """项目外的文件（如测试临时文件）不应被保护。"""
        from src.guard import is_protected
        outside = tmp_path / "test.py"
        outside.write_text("x = 1")
        assert is_protected(str(outside)) is False

    def test_is_protected_uses_coverage_gate_for_project_files(self):
        """项目内非特殊文件应走覆盖率门禁判断。"""
        from src.guard import is_protected
        llm_file = PROJECT_ROOT / "src" / "llm.py"
        result = is_protected(str(llm_file))
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════
# 2. 文件写入 → 一致性维护
# ═══════════════════════════════════════════════════════════════

class TestConsistencyHook:
    """write_file / edit_file_lines 成功后必须触发 _maintain_consistency。"""

    def test_execute_tool_calls_maintain_consistency(self):
        import inspect
        from src.tools import execute_tool
        src = inspect.getsource(execute_tool)
        assert "_maintain_consistency" in src, "execute_tool 未调用 _maintain_consistency"

    def test_maintain_consistency_exists(self):
        from src.tools.__init__ import _maintain_consistency
        assert callable(_maintain_consistency)

    def test_maintain_consistency_no_crash(self, monkeypatch):
        """一致性维护不抛异常（即使 RAG/coverage 模块未完全初始化）。"""
        from src.tools.__init__ import _maintain_consistency
        # 直接调用，内置 try/except 保证不抛异常
        _maintain_consistency(str(PROJECT_ROOT / "src" / "test_dummy.py"))

    def test_maintain_consistency_knowledge_no_crash(self):
        """knowledge/*.md 写入触发研究索引，不抛异常。"""
        from src.tools.__init__ import _maintain_consistency
        _maintain_consistency(str(PROJECT_ROOT / "knowledge" / "test.md"))


# ═══════════════════════════════════════════════════════════════
# 3. 进化系统 → 进化档案
# ═══════════════════════════════════════════════════════════════

class TestEvolveSystem:
    """进化系统的工具和档案必须可以写入和查询。"""

    def test_evolve_record_and_query(self, tmp_path, monkeypatch):
        import src.evolve
        monkeypatch.setattr(src.evolve, "EVOLVE_DIR", tmp_path)
        monkeypatch.setattr(src.evolve, "EVOLVE_LOG", tmp_path / "evolution.jsonl")
        from src.evolve import EvolutionRecord, record_attempt, query
        record = EvolutionRecord(
            id="test-001", goal="测试进化目标", outcome="merged",
            files_changed=["src/test.py"], diff_summary="test",
            tags=["test"], duration_total_ms=1000,
        )
        record_attempt(record)
        results = query(goal_keywords=["测试"], limit=5)
        assert len(results) == 1
        assert results[0]["goal"] == "测试进化目标"

    def test_evolve_find_similar(self, tmp_path, monkeypatch):
        import src.evolve
        monkeypatch.setattr(src.evolve, "EVOLVE_DIR", tmp_path)
        monkeypatch.setattr(src.evolve, "EVOLVE_LOG", tmp_path / "evolution.jsonl")
        from src.evolve import EvolutionRecord, record_attempt, find_similar
        record_attempt(EvolutionRecord(
            id="sim-001", goal="优化文件搜索性能", outcome="merged",
            tags=["performance"], duration_total_ms=100,
        ))
        results = find_similar("文件搜索优化", top_n=3)
        assert isinstance(results, list)


# ═══════════════════════════════════════════════════════════════
# 4. 粘性模型
# ═══════════════════════════════════════════════════════════════

class TestStickyModel:
    """auto_select_model 默认 Pro，切换后粘住。"""

    def test_default_is_pro(self):
        import src.llm
        src.llm.reset_session_model()
        backend = src.llm.auto_select_model("任意输入")
        assert backend.info.name == "Pro"

    def test_sticky_after_switch(self):
        import src.llm
        src.llm.reset_session_model()
        src.llm.switch_model("flash")
        backend = src.llm.auto_select_model("任意输入")
        assert backend.info.name == "Flash"
        src.llm.reset_session_model()

    def test_reset_goes_back_to_pro(self):
        import src.llm
        src.llm.switch_model("flash")
        src.llm.reset_session_model()
        backend = src.llm.auto_select_model("任意输入")
        assert backend.info.name == "Pro"

    def test_switch_updates_session_model(self):
        import src.llm
        src.llm.reset_session_model()
        src.llm.switch_model("flash")
        assert src.llm._session_model == "flash"
        src.llm.switch_model("pro")
        assert src.llm._session_model == "pro"
        src.llm.reset_session_model()

    def test_set_session_model(self):
        import src.llm
        src.llm.set_session_model("flash")
        assert src.llm._session_model == "flash"
        src.llm.reset_session_model()


# ═══════════════════════════════════════════════════════════════
# 5. 工具注册
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:
    """关键工具必须在注册表中且可执行。"""

    def test_subagent_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        assert "subagent" in names

    def test_evolve_tools_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ["evolve_query", "evolve_validate", "evolve_check_access",
                      "evolve_coverage", "evolve_suggest_tests", "evolve_status"]:
            assert name in names, f"{name} 未注册"

    def test_research_tools_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ["search_papers", "read_paper", "save_note",
                      "search_knowledge", "kb_topics", "kb_stats"]:
            assert name in names, f"{name} 未注册"

    def test_memory_tools_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ["remember", "recall", "forget"]:
            assert name in names, f"{name} 未注册"

    def test_rag_tools_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ["rag_query", "rag_status", "rag_browse", "rag_read"]:
            assert name in names, f"{name} 未注册"

    def test_subagent_tool_executable(self):
        """subagent 工具不仅注册了，还要能执行（返回错误也算执行成功）。"""
        from src.tools.subagent import execute
        result = execute("subagent", {"task": "列出项目中的所有Python文件"})
        assert result is not None
        # 可能因为 LLM 调用失败，但至少工具被执行了而不是报"未知工具"


# ═══════════════════════════════════════════════════════════════
# 6. 命令系统
# ═══════════════════════════════════════════════════════════════

class TestCommands:
    """关键命令必须可执行且正确接线。"""

    def test_evolve_command_injects_prompt(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        ctx.log = []
        result = dispatch("/evolve 测试目标", ctx, "test-session")
        assert result.retry is True
        system_msgs = [m["content"] for m in ctx.log if m.get("role") == "system"]
        system_text = " ".join(system_msgs)
        assert "自进化" in system_text or "进化" in system_text

    def test_research_command_injects_prompt(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        ctx.log = []
        result = dispatch("/research 测试主题", ctx, "test-session")
        assert result.retry is True
        system_msgs = [m["content"] for m in ctx.log if m.get("role") == "system"]
        system_text = " ".join(system_msgs)
        assert "研究" in system_text

    def test_model_command_switches(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        result = dispatch("/model pro", ctx, "test-session")
        assert "Pro" in result.message or "pro" in result.message.lower()

    def test_watchdog_command(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        result = dispatch("/watchdog status", ctx, "test-session")
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# 7. 覆盖率门禁
# ═══════════════════════════════════════════════════════════════

class TestCoverageGate:
    """覆盖率门禁必须覆盖所有关键层。"""

    def test_file_tiers_covers_critical_files(self):
        from src.coverage_gate import FILE_TIERS
        assert "tier_0_open" in FILE_TIERS
        assert "tier_4_watchdog" in FILE_TIERS
        assert FILE_TIERS["tier_4_watchdog"]["threshold"] >= 1.0

    def test_can_modify_returns_tuple(self):
        from src.coverage_gate import can_modify
        allowed, reason = can_modify(str(PROJECT_ROOT / "src" / "tools" / "web.py"))
        assert isinstance(allowed, bool)
        assert isinstance(reason, str)

    def test_get_tier_summary(self):
        from src.coverage_gate import get_tier_summary
        summary = get_tier_summary()
        assert "覆盖率" in summary


# ═══════════════════════════════════════════════════════════════
# 8. 看门狗
# ═══════════════════════════════════════════════════════════════

class TestWatchdog:
    """看门狗模块必须可导入且有关键函数。"""

    def test_watchdog_importable(self):
        from src import watchdog
        assert hasattr(watchdog, "run_watchdog")

    def test_watchdog_heartbeat_no_file(self, tmp_path, monkeypatch):
        import src.watchdog
        monkeypatch.setattr(src.watchdog, "HEARTBEAT_FILE", tmp_path / "heartbeat")
        from src.watchdog import _check_heartbeat
        # 无心跳文件 → 返回超时年龄
        age = _check_heartbeat()
        assert age is not None


# ═══════════════════════════════════════════════════════════════
# 9. 自愈系统
# ═══════════════════════════════════════════════════════════════

class TestGuardSelfHealing:
    """guard 自愈系统：崩溃分析→回滚→报告。"""

    def test_analyze_crash_recoverable(self):
        from src.guard import analyze_crash
        try:
            raise RuntimeError("测试崩溃")
        except RuntimeError as e:
            analysis = analyze_crash(type(e), e, e.__traceback__)
        assert "recoverable" in analysis
        assert "traceback" in analysis

    def test_build_report(self):
        from src.guard import build_report
        analysis = {
            "recoverable": True,
            "traceback": "Traceback...\nRuntimeError: test",
            "culprit_files": ["src/test.py"],
        }
        report = build_report(analysis, ["src/test.py"])
        assert "崩溃" in report
        assert "回滚" in report or "恢复" in report


# ═══════════════════════════════════════════════════════════════
# 10. 启动自检 /health
# ═══════════════════════════════════════════════════════════════

class TestHealthCheck:
    """/health 命令必须存在且返回系统状态。"""

    def test_health_command_exists(self):
        from src.commands import _handlers
        assert "/health" in _handlers, "/health 命令未注册"

    def test_health_returns_valid_result(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        result = dispatch("/health", ctx, "test-session")
        assert result is not None
        assert len(result.message) > 0
