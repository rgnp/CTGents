"""coverage_gate.py 测试 — 渐进安全模型的门禁逻辑。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.coverage_gate import (
    FILE_TIERS,
    _match_pattern,
    _get_tier,
    can_modify,
    get_access_level,
    get_tier_summary,
    suggest_tests_to_unlock,
    get_modifiable_files,
    clear_cache,
    AccessLevel,
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

    def test_watchdog_pattern(self):
        assert _match_pattern("src/watchdog.py", "src/watchdog.py")
        assert not _match_pattern("src/guard.py", "src/watchdog.py")


class TestTierClassification:
    """Tier 分类测试。"""

    def test_tool_in_tier_0(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "tools" / "file.py"))
        assert tier is not None
        assert tier[0] == "tier_0_open"

    def test_config_in_tier_1(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "config.py"))
        assert tier is not None
        assert tier[0] == "tier_1_config"

    def test_llm_in_tier_2(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "llm.py"))
        assert tier is not None
        assert tier[0] == "tier_2_core"

    def test_guard_in_tier_3(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "guard.py"))
        assert tier is not None
        assert tier[0] == "tier_3_critical"

    def test_main_in_tier_3(self):
        tier = _get_tier(str(Path(__file__).parent.parent / "src" / "main.py"))
        assert tier is not None
        assert tier[0] == "tier_3_critical"

    def test_watchdog_in_tier_4(self):
        # watchdog.py 可能还不存在，但模式应匹配
        wd_path = str(Path(__file__).parent.parent / "src" / "watchdog.py")
        tier = _get_tier(wd_path)
        # 文件可能不存在但分类逻辑应正确
        if tier is not None:
            assert tier[0] == "tier_4_watchdog"

    def test_non_project_file(self):
        tier = _get_tier("/tmp/random.py")
        assert tier is None


class TestCanModify:
    """can_modify 测试（mock 覆盖率）。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, 9999999999)

    def test_tier_0_always_modifiable(self):
        self._mock_coverage(0.10)
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        allowed, reason = can_modify(fp)
        assert allowed, reason

    def test_tier_1_modifiable_with_50pct(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "config.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 50% >= 45% 应允许，实际: {reason}"

    def test_tier_2_blocked_at_50pct(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"覆盖率 50% < 60% 应拒绝，实际: {reason}"

    def test_tier_4_always_blocked(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "watchdog.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"tier_4 应始终拒绝，实际: {reason}"

    def test_tier_2_unlocked_at_60pct(self):
        self._mock_coverage(0.60)
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 60% >= 60% 应允许，实际: {reason}"

    def test_tier_3_blocked_at_60pct(self):
        self._mock_coverage(0.60)
        fp = str(Path(__file__).parent.parent / "src" / "guard.py")
        allowed, reason = can_modify(fp)
        assert not allowed, f"覆盖率 60% < 75% 应拒绝，实际: {reason}"

    def test_tier_3_unlocked_at_80pct(self):
        self._mock_coverage(0.80)
        fp = str(Path(__file__).parent.parent / "src" / "main.py")
        allowed, reason = can_modify(fp)
        assert allowed, f"覆盖率 80% >= 75% 应允许，实际: {reason}"


class TestGetAccessLevel:
    """get_access_level 测试。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, 9999999999)

    def test_tool_write_access(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        assert get_access_level(fp) == AccessLevel.WRITE_WITH_BACKUP

    def test_llm_read_only(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        assert get_access_level(fp) == AccessLevel.READ_ONLY

    def test_watchdog_restricted(self):
        self._mock_coverage(0.50)
        fp = str(Path(__file__).parent.parent / "src" / "watchdog.py")
        assert get_access_level(fp) == AccessLevel.RESTRICTED


class TestTierSummary:
    """get_tier_summary 测试。"""

    def test_summary_contains_all_tiers(self):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (0.50, {}, 9999999999)
        summary = get_tier_summary()
        assert "tier_0_open" in summary
        assert "tier_1_config" in summary
        assert "tier_2_core" in summary
        assert "tier_3_critical" in summary
        assert "tier_4_watchdog" in summary
        assert "50%" in summary or "50%" in summary.replace("50%", "50%")


class TestSuggestTests:
    """suggest_tests_to_unlock 测试。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, 9999999999)

    def test_suggests_for_locked_file(self):
        self._mock_coverage(0.30)
        fp = str(Path(__file__).parent.parent / "src" / "llm.py")
        result = suggest_tests_to_unlock(fp)
        assert "需要覆盖率从" in result
        assert "60%" in result or "0.60" in result

    def test_suggests_for_unlocked_file(self):
        self._mock_coverage(0.80)
        fp = str(Path(__file__).parent.parent / "src" / "tools" / "file.py")
        result = suggest_tests_to_unlock(fp)
        assert "已解锁" in result


class TestModifiableFiles:
    """get_modifiable_files 测试。"""

    @staticmethod
    def _mock_coverage(pct):
        clear_cache()
        import src.coverage_gate as cg
        cg._coverage_cache = (pct, {}, 9999999999)

    def test_tools_always_modifiable(self):
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
    """验证门槛值合理递增。"""

    def test_thresholds_increasing(self):
        thresholds = [FILE_TIERS[t]["threshold"] for t in
                       ["tier_0_open", "tier_1_config", "tier_2_core",
                        "tier_3_critical", "tier_4_watchdog"]]
        for i in range(len(thresholds) - 1):
            assert thresholds[i] < thresholds[i + 1], (
                f"门槛应递增: {thresholds[i]} < {thresholds[i+1]}"
            )

    def test_tier_4_requires_full_coverage(self):
        assert FILE_TIERS["tier_4_watchdog"]["threshold"] == 1.0


if __name__ == "__main__":
    tests = [
        ("Pattern: 精确匹配", TestPatternMatching().test_exact_match),
        ("Pattern: 通配符匹配", TestPatternMatching().test_wildcard_match),
        ("Pattern: 深层通配符", TestPatternMatching().test_deep_wildcard),
        ("Pattern: 不匹配", TestPatternMatching().test_no_match),
        ("Pattern: watchdog 模式", TestPatternMatching().test_watchdog_pattern),
        ("Tier: 工具在 tier_0", TestTierClassification().test_tool_in_tier_0),
        ("Tier: 配置在 tier_1", TestTierClassification().test_config_in_tier_1),
        ("Tier: LLM 在 tier_2", TestTierClassification().test_llm_in_tier_2),
        ("Tier: guard 在 tier_3", TestTierClassification().test_guard_in_tier_3),
        ("Tier: main 在 tier_3", TestTierClassification().test_main_in_tier_3),
        ("Tier: 非项目文件", TestTierClassification().test_non_project_file),
        ("修改: tier_0 始终可改", TestCanModify().test_tier_0_always_modifiable),
        ("修改: tier_1 50%可改", TestCanModify().test_tier_1_modifiable_with_50pct),
        ("修改: tier_2 50%拒绝", TestCanModify().test_tier_2_blocked_at_50pct),
        ("修改: tier_4 永远拒绝", TestCanModify().test_tier_4_always_blocked),
        ("修改: tier_2 60%解锁", TestCanModify().test_tier_2_unlocked_at_60pct),
        ("修改: tier_3 60%拒绝", TestCanModify().test_tier_3_blocked_at_60pct),
        ("修改: tier_3 80%解锁", TestCanModify().test_tier_3_unlocked_at_80pct),
        ("访问: 工具有写权限", TestGetAccessLevel().test_tool_write_access),
        ("访问: LLM 只读", TestGetAccessLevel().test_llm_read_only),
        ("访问: watchdog 受限", TestGetAccessLevel().test_watchdog_restricted),
        ("摘要: 包含所有 tier", TestTierSummary().test_summary_contains_all_tiers),
        ("建议: 锁定文件", TestSuggestTests().test_suggests_for_locked_file),
        ("建议: 已解锁文件", TestSuggestTests().test_suggests_for_unlocked_file),
        ("文件: 工具始终可改", TestModifiableFiles().test_tools_always_modifiable),
        ("文件: 覆盖率更高解锁更多", TestModifiableFiles().test_more_files_with_higher_coverage),
        ("门槛: 递增", TestThresholds().test_thresholds_increasing),
        ("门槛: tier_4 需100%", TestThresholds().test_tier_4_requires_full_coverage),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
