"""集成测试 — 验证跨模块接线是否闭合。

这些测试不测单个函数逻辑，只测 A→B 的调用链是否正确连接。
"""

from pathlib import Path

import pytest

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
# 3. 粘性模型
# ═══════════════════════════════════════════════════════════════

class TestStickyModel:
    """模型选择：默认 Pro，/model 切换后 auto_select 跟随（全局生效）。

    隔离 fixture：每个用例用 tmp 持久化文件(不写真 .ctg_model)+前后复位 _current_backend，
    切模型是模块全局，不隔离会污染其他测试（test_default_is_pro 曾被串成 flash）。
    """

    @pytest.fixture(autouse=True)
    def _isolate_model(self, tmp_path, monkeypatch):
        import src.llm
        monkeypatch.setattr(src.llm, "_MODEL_PREF_FILE", tmp_path / ".ctg_model")
        saved = src.llm._current_backend
        src.llm._current_backend = src.llm.AVAILABLE_MODELS["pro"]
        yield
        src.llm._current_backend = saved

    def test_default_is_pro(self):
        import src.llm
        assert "pro" in src.llm.auto_select_model("任意输入").info.name.lower()

    def test_auto_select_follows_switch(self):
        """切到 flash 后 auto_select 返回 flash（修复：曾写死 pro→/model 是摆设）。"""
        import src.llm
        src.llm.switch_model("flash")
        assert "flash" in src.llm.auto_select_model("x").info.name.lower()

    def test_model_choice_persists_and_loads(self, tmp_path):
        """切换落盘、load_persisted_model 启动时读回——全局、跨重启。"""
        import src.llm
        src.llm.switch_model("flash")
        assert (tmp_path / ".ctg_model").read_text(encoding="utf-8").strip() == "flash"
        src.llm._current_backend = src.llm.AVAILABLE_MODELS["pro"]  # 模拟重启复位
        src.llm.load_persisted_model()
        assert src.llm.get_current_model_name().lower() == "flash"

# ═══════════════════════════════════════════════════════════════
# 5. 工具注册
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:
    """关键工具必须在注册表中且可执行。"""

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

    def test_model_command_switches(self, tmp_path, monkeypatch):
        import src.llm
        monkeypatch.setattr(src.llm, "_MODEL_PREF_FILE", tmp_path / ".ctg_model")  # 不写真文件
        from src.cache_context import CacheContext
        from src.commands import dispatch

        ctx = CacheContext()
        result = dispatch("/model pro", ctx, "test-session")
        assert "Pro" in result.message or "pro" in result.message.lower()

