"""集成测试 — 验证跨模块接线是否闭合。

这些测试不测单个函数逻辑，只测 A→B 的调用链是否正确连接。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ═══════════════════════════════════════════════════════════════
# 1. 保护系统 → 覆盖率门禁
# ═══════════════════════════════════════════════════════════════

class TestGuardCoverageGate:
    """guard.is_protected() 保护关键文件不被修改。"""

    def test_is_protected_always_blocks_guard_itself(self):
        from src.guard import _GUARD_FILE, is_protected
        assert is_protected(str(_GUARD_FILE)) is True

    def test_is_protected_allows_outside_project(self, tmp_path):
        """项目外的文件（如测试临时文件）不应被保护。"""
        from src.guard import is_protected
        outside = tmp_path / "test.py"
        outside.write_text("x = 1")
        assert is_protected(str(outside)) is False

    def test_is_protected_project_file(self):
        """项目内非特殊文件返回 bool。"""
        from src.guard import is_protected
        llm_file = PROJECT_ROOT / "src" / "llm.py"
        result = is_protected(str(llm_file))
        assert isinstance(result, bool)

# ═══════════════════════════════════════════════════════════════
# 3. 进化系统 → 进化档案
# ═══════════════════════════════════════════════════════════════

class TestEvolveSystem:
    """进化系统的工具和档案必须可以写入和查询。"""

    def test_evolve_record_and_query(self, tmp_path, monkeypatch):
        import src.evolve
        monkeypatch.setattr(src.evolve, "EVOLVE_DIR", tmp_path)
        monkeypatch.setattr(src.evolve, "EVOLVE_LOG", tmp_path / "evolution.jsonl")
        from src.evolve import EvolutionRecord, query, record_attempt
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
        from src.evolve import EvolutionRecord, find_similar, record_attempt
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
    """始终使用 Pro 模型。"""

    def test_default_is_pro(self):
        import src.llm
        backend = src.llm.auto_select_model("任意输入")
        assert "pro" in backend.info.name.lower()

    def test_always_returns_pro(self):
        import src.llm
        backend = src.llm.auto_select_model("任意输入")
        assert "pro" in backend.info.name.lower()

# ═══════════════════════════════════════════════════════════════
# 5. 工具注册
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:
    """关键工具必须在注册表中且可执行。"""

    def test_evolve_tools_registered(self):
        from src.tools import get_tools
        tools = get_tools()
        names = [t["function"]["name"] for t in tools]
        for name in ["evolve_query", "evolve_validate", "evolve_check_access",
                      "evolve_coverage", "evolve_suggest_tests", "evolve_status"]:
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
        for name in ["rag_index", "rag_query", "rag_status"]:
            assert name in names, f"{name} 未注册"

    """关键命令必须可执行且正确接线。"""

    def test_evolve_command_injects_prompt(self, tmp_path, monkeypatch):
        import src.evolution_runner as runner
        from src.cache_context import CacheContext
        from src.commands import dispatch

        run_root = tmp_path / "evolution"
        monkeypatch.setattr(runner, "RUN_ROOT", run_root)
        monkeypatch.setattr(runner, "RUNS_DIR", run_root / "runs")
        monkeypatch.setattr(runner, "ACTIVE_RUN_FILE", run_root / "active.json")

        ctx = CacheContext()
        ctx.log = []
        result = dispatch("/evolve 测试目标", ctx, "test-session")
        assert result.retry is True
        system_msgs = [m["content"] for m in ctx.log if m.get("role") == "system"]
        system_text = " ".join(system_msgs)
        assert "Runner" in system_text
        assert runner.load_active_evolution_run() is not None

    def test_model_command_switches(self):
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        result = dispatch("/model pro", ctx, "test-session")
        assert "Pro" in result.message or "pro" in result.message.lower()

# ═══════════════════════════════════════════════════════════════
# 7. 覆盖率门禁
# ═══════════════════════════════════════════════════════════════

class TestCoverageGate:
    """覆盖率门禁必须覆盖所有关键层。"""

    def test_file_tiers_covers_critical_files(self):
        from src.coverage_gate import FILE_TIERS
        assert "tier_0_open" in FILE_TIERS
        assert "tier_3_critical" in FILE_TIERS
        assert FILE_TIERS["tier_3_critical"]["threshold"] >= 0.75

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
# 7. 覆盖率门禁
# ═══════════════════════════════════════════════════════════════
