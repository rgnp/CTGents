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
    format_report,
    post_test_checks,
    pre_commit_checks,
)


class TestASTCheck:
    """AST 语法检查测试。"""

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
        assert "SyntaxError" in err or "语法错误" in err

    def test_nonexistent_file(self):
        err = _ast_check("/nonexistent/path.py")
        assert err is not None
        assert "不存在" in err

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        err = _ast_check(str(f))
        assert err is None


class TestPreCommitChecks:
    """Phase 1 静态检查测试。"""

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
        assert "错误" in result.details or "SyntaxError" in result.details

    def test_empty_list(self):
        result = pre_commit_checks([])
        assert result.result == Result.PASS

    def test_non_python_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello", encoding="utf-8")
        result = pre_commit_checks([str(f)])
        assert result.result == Result.PASS


class TestPostTestChecks:
    """Phase 3 后检查测试。"""

    def test_coverage_not_decreased(self):
        result = post_test_checks(0.50, 0.52, 3, 2)
        assert result.result == Result.PASS

    def test_coverage_decreased(self):
        result = post_test_checks(0.50, 0.40, 3, 3)
        assert result.result == Result.FAIL
        assert "下降" in result.details

    def test_coverage_same(self):
        result = post_test_checks(0.50, 0.50, 3, 3)
        assert result.result == Result.PASS

    def test_new_lint_errors(self):
        result = post_test_checks(0.50, 0.55, 3, 7)
        assert result.result == Result.FAIL
        assert "lint" in result.details.lower()

    def test_lint_decreased(self):
        result = post_test_checks(0.50, 0.55, 3, 1)
        assert result.result == Result.PASS

    def test_both_issues(self):
        result = post_test_checks(0.60, 0.40, 0, 5)
        assert result.result == Result.FAIL
        assert "下降" in result.details
        assert "lint" in result.details.lower()


class TestValidationReport:
    """ValidationReport 数据类测试。"""

    def test_report_creation(self):
        report = ValidationReport(
            overall=Result.PASS,
            phases=[
                PhaseResult(phase=Phase.PRE_COMMIT, result=Result.PASS, details="ok"),
                PhaseResult(phase=Phase.SANDBOX_TEST, result=Result.PASS, details="passed"),
                PhaseResult(phase=Phase.POST_TEST, result=Result.PASS, details="clean"),
            ],
            files_changed=["src/foo.py"],
            coverage_before=0.45,
            coverage_after=0.46,
        )
        assert report.overall == Result.PASS
        assert len(report.phases) == 3

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
        assert "PRE_COMMIT" in formatted.lower() or "pre_commit" in formatted
        assert "0.45" in formatted or "45" in formatted


class TestPhaseEnum:
    """枚举测试。"""

    def test_phase_values(self):
        assert Phase.PRE_COMMIT.value == "pre_commit"
        assert Phase.SANDBOX_TEST.value == "sandbox_test"
        assert Phase.POST_TEST.value == "post_test"

    def test_result_values(self):
        assert Result.PASS.value == "pass"
        assert Result.FAIL.value == "fail"
        assert Result.TIMEOUT.value == "timeout"
        assert Result.SKIP.value == "skip"


if __name__ == "__main__":
    import inspect

    tests = []
    for cls in [TestASTCheck, TestPreCommitChecks, TestPostTestChecks,
                TestValidationReport, TestPhaseEnum]:
        # 处理需要 tmp_path fixture 的测试
        needs_fixture = "tmp_path" in inspect.signature(cls.__init__).parameters if hasattr(cls, '__init__') else False
        for name in dir(cls):
            if name.startswith("test_"):
                tests.append((f"{cls.__name__}.{name}", getattr(cls(), name)))

    passed = 0
    for name, fn in tests:
        try:
            sig = inspect.signature(fn)
            # 检查是否需要用 tmp_path
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
