"""coverage_gate.py 测试 — 函数级关联测试门禁逻辑。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.coverage_gate import (
    FILE_TIERS,
    _get_tier,
    _match_pattern,
    can_modify,
    clear_cache,
    get_modifiable_files,
    get_tier_summary,
    suggest_tests_to_unlock,
)


class TestPatternMatching:
    """glob 模式匹配测试。"""

    def test_exact_match(self):
        assert _match_pattern("src/config.py", "src/config.py")

    def test_wildcard_match(self):
        assert _match_pattern("src/tools/file.py", "src/tools/*.py")
        assert _match_pattern("src/tools/web.py", "src/tools/*.py")

    def test_deep_wildcard(self):
        assert _match_pattern("src/tools/sub/file.py", "src/tools/**/*.py")

    def test_no_match(self):
        assert not _match_pattern("src/main.py", "src/tools/*.py")
        assert not _match_pattern("tests/test.py", "src/*.py")


class TestTierClassification:
    """Tier 分类测试（三 tier：0/1_config/2_core/3_critical）。"""

    def test_tool_in_tier_0(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "tools" / "file.py"))
        assert tier is not None
        assert tier[0] == "tier_0_open"

    def test_config_in_tier_1(self):
        fp = str(Path(__file__).parent.parent / "src" / "config.py")
        assert _get_tier(fp)[0] == "tier_1_config"

    def test_llm_in_tier_2(self):
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        assert _get_tier(fp)[0] == "tier_2_core"

    def test_guard_in_tier_3(self):
        fp = str(Path(__file__).parent.parent / "src" / "guard.py")
        assert _get_tier(fp)[0] == "tier_3_critical"

    def test_main_in_tier_3(self):
        fp = str(Path(__file__).parent.parent / "src" / "main.py")
        assert _get_tier(fp)[0] == "tier_3_critical"

    def test_non_project_file(self):
        tier = _get_tier("/tmp/random.py")
        assert tier is None


class TestCanModify:
    """can_modify 测试（mock 覆盖率）。"""

    @staticmethod
    def _mock_coverage(pct, file_cov=None):
        clear_cache()
        import src.coverage_gate as cg
        if file_cov is None:
            file_cov = {}
        cg._coverage_cache = (pct, file_cov, {}, 9999999999)

    def test_tier_0_always_modifiable(self):
        self._mock_coverage(0.10)
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        allowed, reason = can_modify(fp)
        assert allowed, reason

    def test_tier_1_modifiable_with_50pct(self):
        self._mock_coverage(0.50, {"src/config.py": 0.50})
        fp = str(Path(__file__).parent.parent / "src" / "config.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 50% >= 45% 应允许: {reason}"

    def test_tier_2_blocked_at_50pct(self):
        self._mock_coverage(0.50, {"src/llm.py": 0.50})
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"覆盖率 50% < 60% 应拒绝，实际: {reason}"

    def test_tier_2_unlocked_at_60pct(self):
        self._mock_coverage(0.60, {"src/llm.py": 0.60})
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 60% >= 60% 应允许: {reason}"

    def test_tier_3_blocked_at_60pct(self):
        self._mock_coverage(0.60, {"src/guard.py": 0.60})
        fp = str(Path(__file__).parent.parent / "src" / "guard.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"覆盖率 60% < 75% 应拒绝: {reason}"

    def test_tier_3_unlocked_at_80pct(self):
        self._mock_coverage(0.80, {"src/guard.py": 0.80})
        fp = str(Path(__file__).parent.parent / "src" / "guard.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 80% >= 75% 应允许: {reason}"

    def test_non_project_file(self):
        allowed, reason = can_modify("/tmp/x.py")
        assert allowed, f"项目外文件不在门禁管辖范围，应放行: {reason}"

    def test_file_not_in_coverage_data_allowed(self):
        """文件不在覆盖率数据中（新文件）→ 放行。"""
        self._mock_coverage(0.80)  # file_cov 为空
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"新文件（不在覆盖率数据中）应放行: {reason}"

    def test_file_zero_coverage_blocked(self):
        """文件有 0% 逐文件覆盖率 → 拒绝。"""
        self._mock_coverage(0.80, {"src/llm.py": 0.0})
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"逐文件覆盖率 0% 应拒绝: {reason}"


class TestTierSummary:
    """get_tier_summary 测试。"""

    def test_summary_contains_all_tiers(self):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (0.50, {}, {}, 9999999999)
        summary = get_tier_summary()
        assert "tier_0_open" in summary
        assert "tier_1_config" in summary
        assert "tier_2_core" in summary
        assert "tier_3_critical" in summary


class TestSuggestTests:
    """suggest_tests_to_unlock 测试。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, {}, 9999999999)

    def test_suggests_for_locked_file(self):
        self._mock_coverage(0.30)
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        result = suggest_tests_to_unlock(fp)
        assert "60%" in result or "0.60" in result
        assert "30%" in result or "0.30" in result

    def test_suggests_for_unlocked_file(self):
        self._mock_coverage(0.80)
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        result = suggest_tests_to_unlock(fp)
        assert "始终可修改" in result


class TestModifiableFiles:
    """get_modifiable_files 测试。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, {}, 9999999999)

    def test_tier_0_always_included(self):
        self._mock_coverage(0.10)
        files = get_modifiable_files()
        tool_files = [f for f in files if f.startswith("src/tools/")]
        assert len(tool_files) > 0, "tier_0 不需要覆盖率，工具文件应始终可修改"

    def test_more_files_with_higher_coverage(self):
        self._mock_coverage(0.10)
        low = len(get_modifiable_files())
        self._mock_coverage(0.50)
        mid = len(get_modifiable_files())
        assert mid >= low, "覆盖率更高时应至少解锁同样多的文件"


class TestThresholds:
    """验证门槛值合理递增（三 tier）。"""

    def test_thresholds_increasing(self):
        thresholds = [FILE_TIERS[t]["threshold"] for t in
                       ["tier_0_open", "tier_1_config", "tier_2_core",
                        "tier_3_critical"]]
        for i in range(len(thresholds) - 1):
            assert thresholds[i] < thresholds[i + 1], (
                f"门槛应递增: {thresholds[i]} < {thresholds[i+1]}"
            )

    def test_tier_3_requires_75pct(self):
        assert FILE_TIERS["tier_3_critical"]["threshold"] == 0.75


class TestFunctionLevelCheck:
    """函数级关联测试检查 — 核心新功能测试。"""

    @staticmethod
    def _setup_cache(pct, covered_lines=None):
        clear_cache()
        import src.coverage_gate as cg
        line_data = {"src/commands.py": covered_lines or set()}
        cg._coverage_cache = (pct, {"src/commands.py": pct}, line_data, 9999999999)

    def test_function_level_no_touched_functions_falls_back(self):
        """不提供 touched_functions 时回退到全局覆盖率检查。"""
        self._setup_cache(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "commands.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"50% >= 45% 应允许: {reason}"

    def test_function_level_covered_allows(self):
        """要改的函数有测试覆盖 → 放行。"""
        self._setup_cache(0.30, {50, 51, 52, 53})  # 覆盖了 _add_cmd 函数（第50行起）
        fp = str(Path(__file__).parent.parent / "src" / "commands.py")
        allowed, reason = can_modify(fp, touched_functions=["_add_cmd"])
        assert allowed, f"函数有覆盖应允许: {reason}"

    def test_function_level_uncovered_blocks(self):
        """要改的函数没测试覆盖 → 拒绝并列出函数名。"""
        self._setup_cache(0.30, set())
        fp = str(Path(__file__).parent.parent / "src" / "commands.py")
        allowed, reason = can_modify(fp, touched_functions=["dispatch"])
        assert not allowed, f"无覆盖应拒绝: {reason}"
        assert "dispatch" in reason

    def test_function_level_mixed_coverage(self):
        """部分有覆盖 → 拒绝并精确指出哪个函数没覆盖。"""
        self._setup_cache(0.30, set(range(50, 61)))  # 只覆盖 _add_cmd
        fp = str(Path(__file__).parent.parent / "src" / "commands.py")
        allowed, reason = can_modify(fp, touched_functions=["_add_cmd", "dispatch"])
        assert not allowed
        assert "dispatch" in reason

    def test_function_level_tier_0_always_ok(self):
        """Tier 0 文件不走函数级检查，始终放行。"""
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        allowed, reason = can_modify(fp, touched_functions=["write_file"])
        assert allowed, f"Tier 0 应始终放行: {reason}"

    def test_suggest_with_touched_functions(self):
        """suggest_tests_to_unlock 支持函数级精确建议。"""
        self._setup_cache(0.30, set())
        fp = str(Path(__file__).parent.parent / "src" / "commands.py")
        result = suggest_tests_to_unlock(fp, touched_functions=["dispatch"])
        assert "dispatch" in result
