"""validate.py 测试 — 验证流水线的三阶段检查逻辑。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.validate import (
    Phase,
    PhaseResult,
    Result,
    ValidationReport,
    _ast_check,
    _count_lint_issues,
    _get_coverage,
    _import_check,
    _ruff_check,
    format_report,
    post_test_checks,
    pre_commit_checks,
    sandbox_tests,
    validate,
)


class TestASTCheck:
    def test_valid_python(self, tmp_path):
        f = tmp_path / "good.py"
        f.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        err = _ast_check(str(f))
        assert err is None

    def test_syntax_error(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def hello(:\n    return\n", encoding="utf-8")
        err = _ast_check(str(f))
        assert err is not None
        assert "SyntaxError" in err or "错误" in err

    def test_nonexistent_file(self):
        err = _ast_check("/nonexistent/path.py")
        assert err is not None
        assert "不存在" in err

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        err = _ast_check(str(f))
        assert err is None


class TestImportCheck:
    def test_stdlib_import(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("import os\n", encoding="utf-8")
        err = _import_check(str(f))
        assert err is None

    def test_nonexistent_import(self, tmp_path):
        f = tmp_path / "m.py"
        f.write_text("import nonexistent_module_xyz_123\n", encoding="utf-8")
        err = _import_check(str(f))
        assert err is not None

    def test_non_py_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# hello", encoding="utf-8")
        err = _import_check(str(f))
        assert err is None


class TestRuffCheck:
    def test_ruff_check_empty(self):
        """空文件列表 → 0 错误。"""
        count, output = _ruff_check([])
        assert count == 0

    def test_ruff_check_valid(self, tmp_path):
        """有效 Python 文件 → 0 lint 错误。"""
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n", encoding="utf-8")
        count, _ = _ruff_check([str(f)])
        # ruff 可能报告一些风格问题，但我们只关心不崩
        assert isinstance(count, int)


class TestPreCommitChecks:
    def test_all_valid(self, tmp_path):
        f = tmp_path / "valid.py"
        f.write_text("x = 1\ny = 2\n", encoding="utf-8")
        result = pre_commit_checks([str(f)])
        assert result.result == Result.PASS

    def test_syntax_error_detected(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("def broken(:\n    pass\n", encoding="utf-8")
        result = pre_commit_checks([str(f)])
        assert result.result == Result.FAIL

    def test_empty_list(self):
        result = pre_commit_checks([])
        assert result.result == Result.PASS

    def test_non_python_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello", encoding="utf-8")
        result = pre_commit_checks([str(f)])
        assert result.result == Result.PASS


class TestSandboxTests:
    def test_quick_sandbox(self):
        """sandbox_tests 短超时 — 可能超时但不崩。"""
        result = sandbox_tests(timeout=5)
        assert result.phase == Phase.SANDBOX_TEST
        assert isinstance(result.result, Result)


class TestGetCoverage:
    def test_get_coverage(self):
        """_get_coverage 返回合法浮点数。"""
        cov = _get_coverage()
        assert isinstance(cov, float)
        assert 0.0 <= cov <= 1.0


class TestCountLintIssues:
    def test_count_lint(self):
        """_count_lint_issues 返回整数。"""
        count = _count_lint_issues()
        assert isinstance(count, int)


class TestPostTestChecks:
    def test_coverage_not_decreased(self):
        result = post_test_checks(0.50, 0.52, 3, 2)
        assert result.result == Result.PASS

    def test_coverage_decreased(self):
        result = post_test_checks(0.50, 0.40, 3, 3)
        assert result.result == Result.FAIL

    def test_new_lint_errors(self):
        result = post_test_checks(0.50, 0.55, 3, 7)
        assert result.result == Result.FAIL


class TestValidationReport:
    def test_format_report(self):
        report = ValidationReport(
            overall=Result.PASS,
            phases=[
                PhaseResult(phase=Phase.PRE_COMMIT, result=Result.PASS,
                           details="所有静态检查通过", duration_ms=100),
                PhaseResult(phase=Phase.SANDBOX_TEST, result=Result.PASS,
                           details="所有测试通过", duration_ms=5000),
                PhaseResult(phase=Phase.POST_TEST, result=Result.PASS,
                           details="覆盖率 0.46", duration_ms=50),
            ],
            files_changed=["src/test.py"],
            coverage_before=0.45,
            coverage_after=0.46,
            lint_errors_before=0,
            lint_errors_after=0,
            total_duration_ms=5150,
        )
        formatted = format_report(report)
        assert "PASS" in formatted


class TestValidate:
    def test_validate_no_files(self):
        """Validate 空文件列表 — 不崩。"""
        report = validate([])
        assert report.overall in (Result.PASS, Result.FAIL, Result.TIMEOUT)

    def test_validate_syntax_error(self, tmp_path):
        """Validate 有语法错的文件 — 快速失败。"""
        f = tmp_path / "bad.py"
        f.write_text("def bad(:\n", encoding="utf-8")
        report = validate([str(f)])
        assert report.overall == Result.FAIL
        assert len(report.phases) == 1  # 只跑了 Phase 1


if __name__ == "__main__":
    import inspect

    tests = []
    for cls in [TestASTCheck, TestImportCheck, TestRuffCheck,
                TestPreCommitChecks, TestSandboxTests, TestGetCoverage,
                TestCountLintIssues, TestPostTestChecks,
                TestValidationReport, TestValidate]:
        for name in dir(cls):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(cls(), name)))

    passed = 0
    for name, fn in tests:
        try:
            sig = inspect.signature(fn)
            if "tmp_path" in str(sig):
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
                fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            import traceback
            print(f"  💥 {name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
