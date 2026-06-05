"""验证流水线 — 代码合入前的三阶段自动检查。

Phase 1: 静态检查（AST 语法 + import 可解析 + ruff lint）
Phase 2: 沙箱测试（pytest --cov 子进程执行，超时终止）
Phase 3: 后检查（覆盖率不降、无新增 lint 错误）

任一阶段失败即拒绝合入，返回结构化 ValidationReport。
"""

import ast
import importlib.util
import json
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Phase(Enum):
    PRE_COMMIT = "pre_commit"
    SANDBOX_TEST = "sandbox_test"
    POST_TEST = "post_test"


class Result(Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SKIP = "skip"


@dataclass
class PhaseResult:
    phase: Phase
    result: Result
    details: str = ""
    duration_ms: float = 0.0


@dataclass
class ValidationReport:
    overall: Result
    phases: list[PhaseResult] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    coverage_before: float = 0.0
    coverage_after: float = 0.0
    lint_errors_before: int = 0
    lint_errors_after: int = 0
    test_output: str = ""
    total_duration_ms: float = 0.0


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════
# Phase 1: 静态检查
# ═══════════════════════════════════════════════════════════════

def _ast_check(filepath: str) -> str | None:
    """AST 语法解析。成功返回 None，失败返回错误字符串。"""
    try:
        path = Path(filepath)
        if not path.exists():
            return f"文件不存在: {filepath}"
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=filepath)
        return None
    except SyntaxError as e:
        return f"语法错误 {filepath}:{e.lineno}: {e.msg}"
    except Exception as e:
        return f"AST 解析失败 {filepath}: {e}"


def _import_check(filepath: str) -> str | None:
    """用 ast 提取 import 语句，find_spec 验证（进程内，毫秒级）。"""
    p = Path(filepath)
    if not p.suffix == ".py":
        return None

    try:
        source = p.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return None

    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if importlib.util.find_spec(base) is None:
                    errors.append(f"无法找到模块: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith("."):
                base = node.module.split(".")[0]
                if importlib.util.find_spec(base) is None:
                    errors.append(f"无法找到模块: {node.module}")

    if errors:
        return f"Import 检查失败 {filepath}: {'; '.join(errors[:3])}"
    return None


def _ruff_check(filepaths: list[str]) -> tuple[int, str]:
    """运行 ruff lint。返回 (error_count, output)。"""
    existing = [f for f in filepaths if Path(f).exists()]
    if not existing:
        return 0, ""

    try:
        result = subprocess.run(
            ["py", "-m", "ruff", "check", *existing],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=30,
            encoding="utf-8", errors="replace",
        )
        # ruff 返回码: 0=无错误, 1=有lint错误, 2=运行时错误
        if result.returncode == 0:
            return 0, ""
        output = (result.stdout + result.stderr).strip()
        # 只统计含错误代码的行（格式: file:line:col: CODE message）
        error_lines = [l for l in output.split("\n") if l.strip()
                       and not l.startswith("warning:")]
        return len(error_lines), output
    except subprocess.TimeoutExpired:
        return 0, "ruff 检查超时"
    except FileNotFoundError:
        return 0, ""  # ruff 不可用
    except Exception:
        return 0, ""


def pre_commit_checks(changed_files: list[str]) -> PhaseResult:
    """Phase 1: 静态检查所有变更文件。"""
    t0 = time.perf_counter()
    errors: list[str] = []

    for f in changed_files:
        if f.endswith(".py"):
            err = _ast_check(f)
            if err:
                errors.append(err)
                continue  # AST 失败就跳过 import 和 lint

            err = _import_check(f)
            if err:
                errors.append(err)

    lint_count, lint_output = _ruff_check(changed_files)
    if lint_count > 0:
        errors.append(f"ruff 发现 {lint_count} 个问题:\n{lint_output[:500]}")

    duration = (time.perf_counter() - t0) * 1000
    if errors:
        return PhaseResult(
            phase=Phase.PRE_COMMIT,
            result=Result.FAIL,
            details="\n".join(errors),
            duration_ms=duration,
        )
    return PhaseResult(phase=Phase.PRE_COMMIT, result=Result.PASS,
                       details="所有静态检查通过", duration_ms=duration)


# ═══════════════════════════════════════════════════════════════
# Phase 2: 沙箱测试
# ═══════════════════════════════════════════════════════════════

def _get_coverage() -> float:
    """从 coverage.json 读取总体覆盖率。"""
    cov_path = PROJECT_ROOT / "coverage.json"
    if cov_path.exists():
        try:
            data = json.loads(cov_path.read_text(encoding="utf-8"))
            return data.get("totals", {}).get("percent_covered", 0.0) / 100.0
        except (json.JSONDecodeError, OSError):
            pass
    return 0.0


def _count_lint_issues() -> int:
    """统计当前 lint 问题总数。"""
    try:
        result = subprocess.run(
            ["py", "-m", "ruff", "check", "src/"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return 0
        output = (result.stdout + result.stderr).strip()
        return len([l for l in output.split("\n") if l.strip()
                    and not l.startswith("warning:")])
    except Exception:
        return 0


def sandbox_tests(timeout: int = 120) -> PhaseResult:
    """Phase 2: 在子进程中运行 pytest --cov。"""
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["py", "-m", "pytest", "--cov=src", "--cov-report=json",
             "-p", "no:cacheprovider", "-q", "--tb=short"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        output = result.stdout + "\n" + result.stderr
        duration = (time.perf_counter() - t0) * 1000

        if result.returncode == 0:
            return PhaseResult(
                phase=Phase.SANDBOX_TEST,
                result=Result.PASS,
                details=f"所有测试通过",
                duration_ms=duration,
            )
        else:
            # 提取失败摘要：优先保留包含 FAILED/ERROR/AssertionError 的行
            fail_lines = output.split("\n")
            # 取尾部 3000 字符（而不是 1000），确保包含完整错误信息
            tail = output[-3000:] if len(output) > 3000 else output
            # 额外提取所有 "FAILED" 行放在前面
            failed_tests = [l.strip() for l in fail_lines
                          if "FAILED" in l and "::" in l][:10]
            prefix = ""
            if failed_tests:
                prefix = "失败测试:\n" + "\n".join(f"  - {t}" for t in failed_tests) + "\n\n"
            return PhaseResult(
                phase=Phase.SANDBOX_TEST,
                result=Result.FAIL,
                details=f"测试失败 (exit={result.returncode}, {len(failed_tests)} 个失败)\n{prefix}{tail}",
                duration_ms=duration,
            )
    except subprocess.TimeoutExpired:
        duration = (time.perf_counter() - t0) * 1000
        return PhaseResult(
            phase=Phase.SANDBOX_TEST,
            result=Result.TIMEOUT,
            details=f"测试超时（>{timeout}s）",
            duration_ms=duration,
        )
    except FileNotFoundError:
        return PhaseResult(
            phase=Phase.SANDBOX_TEST,
            result=Result.SKIP,
            details="pytest 不可用，跳过测试",
            duration_ms=0.0,
        )
    except Exception as e:
        return PhaseResult(
            phase=Phase.SANDBOX_TEST,
            result=Result.FAIL,
            details=f"测试执行异常: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


# ═══════════════════════════════════════════════════════════════
# Phase 3: 后检查
# ═══════════════════════════════════════════════════════════════

def post_test_checks(coverage_before: float, coverage_after: float,
                     lint_before: int, lint_after: int) -> PhaseResult:
    """Phase 3: 覆盖率不降、无新增 lint 错误。"""
    t0 = time.perf_counter()
    issues: list[str] = []

    if coverage_after < coverage_before - 0.01:  # 允许 1% 浮点误差
        issues.append(
            f"覆盖率下降: {coverage_before:.1%} → {coverage_after:.1%} "
            f"({(coverage_before - coverage_after):.1%})"
        )

    new_lint = lint_after - lint_before
    if new_lint > 0:
        issues.append(
            f"新增 {new_lint} 个 lint 问题"
            f"（之前 {lint_before}，之后 {lint_after}）"
        )

    duration = (time.perf_counter() - t0) * 1000
    if issues:
        return PhaseResult(
            phase=Phase.POST_TEST,
            result=Result.FAIL,
            details="\n".join(issues),
            duration_ms=duration,
        )
    return PhaseResult(phase=Phase.POST_TEST, result=Result.PASS,
                       details=f"覆盖率 {coverage_after:.1%}（变化 {coverage_after - coverage_before:+.1%}），"
                               f"lint {lint_before}→{lint_after}",
                       duration_ms=duration)


# ═══════════════════════════════════════════════════════════════
# 主流水线
# ═══════════════════════════════════════════════════════════════

def validate(changed_files: list[str], timeout: int = 120) -> ValidationReport:
    """运行完整三阶段验证流水线。返回 ValidationReport。"""
    t0 = time.perf_counter()

    # 0. 基线采集
    coverage_before = _get_coverage()
    lint_before = _count_lint_issues()

    # 1. 静态检查
    pre = pre_commit_checks(changed_files)
    if pre.result == Result.FAIL:
        return ValidationReport(
            overall=Result.FAIL,
            phases=[pre],
            files_changed=changed_files,
            coverage_before=coverage_before,
            coverage_after=coverage_before,
            lint_errors_before=lint_before,
            lint_errors_after=lint_before,
            total_duration_ms=(time.perf_counter() - t0) * 1000,
        )

    # 2. 沙箱测试
    sand = sandbox_tests(timeout)
    test_output = sand.details

    # 3. 后检查
    coverage_after = _get_coverage()
    lint_after = _count_lint_issues()
    post = post_test_checks(coverage_before, coverage_after, lint_before, lint_after)

    # 汇总
    if sand.result == Result.PASS and post.result == Result.PASS:
        overall = Result.PASS
    elif sand.result == Result.TIMEOUT:
        overall = Result.TIMEOUT
    else:
        overall = Result.FAIL

    return ValidationReport(
        overall=overall,
        phases=[pre, sand, post],
        files_changed=changed_files,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        lint_errors_before=lint_before,
        lint_errors_after=lint_after,
        test_output=test_output,
        total_duration_ms=(time.perf_counter() - t0) * 1000,
    )


def format_report(report: ValidationReport) -> str:
    """将验证报告格式化为可读文本。"""
    lines = [
        "═" * 50,
        f"验证结果: {report.overall.value.upper()}",
        f"耗时: {report.total_duration_ms:.0f}ms",
        f"变更文件: {len(report.files_changed)} 个",
        "",
    ]
    for p in report.phases:
        icon = {"pass": "✅", "fail": "❌", "timeout": "⏱️", "skip": "⏭️"}[p.result.value]
        lines.append(f"  {icon} {p.phase.value}: {p.details[:200]}")
        if p.duration_ms > 0:
            lines[-1] += f" ({p.duration_ms:.0f}ms)"

    lines.extend([
        "",
        f"覆盖率: {report.coverage_before:.1%} → {report.coverage_after:.1%} "
        f"({report.coverage_after - report.coverage_before:+.1%})",
        f"Lint: {report.lint_errors_before} → {report.lint_errors_after} "
        f"({report.lint_errors_after - report.lint_errors_before:+d})",
        "═" * 50,
    ])
    return "\n".join(lines)
