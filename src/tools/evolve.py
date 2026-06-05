"""进化系统工具 — 暴露给 LLM 的自进化能力。

工具列表：
  evolve_query        — 查询进化档案，了解过去的自修改尝试
  evolve_check_access — 检查是否有权限修改指定文件
  evolve_coverage     — 获取当前测试覆盖率报告
  evolve_validate     — 运行验证流水线（静态检查→沙箱测试→后检查）
  evolve_suggest_tests— 获取解锁修改权限的测试建议
"""

import ast
import subprocess
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 关联测试查找：变更文件 → 测试文件
# ═══════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _file_to_module(filepath: str) -> str | None:
    """文件路径 → Python 模块名。"""
    fp = Path(filepath).resolve()
    try:
        rel = fp.relative_to(_PROJECT_ROOT)
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    else:
        return None
    return ".".join(parts)


def _parse_test_imports() -> dict[str, set[str]]:
    """解析所有 test_*.py 的 import，返回 {test_path: {imported_modules}}。"""
    tests_dir = _PROJECT_ROOT / "tests"
    if not tests_dir.is_dir():
        return {}
    cache: dict[str, set[str]] = {}
    for tf in sorted(tests_dir.glob("test_*.py")):
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        cache[str(tf)] = imports
    return cache


def _find_related_tests(changed_files: list[str]) -> tuple[list[str], str]:
    """根据变更文件找到相关测试文件。

    Returns:
        (related_test_paths, info_message)
    """
    target_modules: set[str] = set()
    for f in changed_files:
        mod = _file_to_module(f)
        if mod:
            target_modules.add(mod)
            parts = mod.split(".")
            for i in range(1, len(parts)):
                target_modules.add(".".join(parts[:i]))

    if not target_modules:
        return [], "无法将变更文件映射到模块名。"

    test_imports = _parse_test_imports()
    related: list[str] = []
    for test_path, imports in test_imports.items():
        if imports & target_modules:
            related.append(test_path)

    zero_cov: list[str] = []
    for f in changed_files:
        mod = _file_to_module(f)
        if mod and not any(mod in imps for imps in test_imports.values()):
            zero_cov.append(Path(f).name)

    parts: list[str] = []
    if related:
        parts.append(f"找到 {len(related)} 个相关测试文件")
    else:
        parts.append("未找到任何相关测试文件")
    if zero_cov:
        parts.append(f"⚠️ 零覆盖文件: {', '.join(zero_cov)}")

    return sorted(related), "。".join(parts)


def _run_related_tests(test_files: list[str], timeout: int = 120) -> tuple[bool, str, float]:
    """只运行指定的测试文件，带覆盖率。"""
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            ["py", "-m", "pytest", "--cov=src", "--cov-report=json",
             "-p", "no:cacheprovider", "-q", "--tb=short", *test_files],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        output = result.stdout + "\n" + result.stderr
        output = result.stdout + "\n" + result.stderr
        duration = (time.perf_counter() - t0) * 1000
        if result.returncode == 0:
            return True, output, duration
        failed = [ln.strip() for ln in output.split("\n")
                  if "FAILED" in ln and "::" in ln][:10]
        prefix = ""
        if failed:
            prefix = "失败测试:\n" + "\n".join(f"  - {t}" for t in failed) + "\n\n"
        tail = output[-3000:] if len(output) > 3000 else output
        return False, f"exit={result.returncode}\n{prefix}{tail}", duration
    except subprocess.TimeoutExpired:
        return False, f"测试超时（>{timeout}s）", (time.perf_counter() - t0) * 1000
    except FileNotFoundError:
        return False, "pytest 不可用", 0.0
    except Exception as e:
        return False, f"测试异常: {e}", (time.perf_counter() - t0) * 1000


# ═══════════════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════════════

TOOLS_EVOLVE: list[dict] = [
    {
        "_meta": {"label": "进化查询"},
        "type": "function",
        "function": {
            "name": "evolve_query",
            "description": "查询进化档案，了解过去的成功/失败模式。自修改前先调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键词列表，搜索相似进化记录",
                    },
                    "outcome": {
                        "type": "string",
                        "enum": ["merged", "reverted", "partial"],
                        "description": "按结果筛选：merged/reverted/partial",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按标签筛选，如 performance/bugfix",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回条数，默认 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "权限检查"},
        "type": "function",
        "function": {
            "name": "evolve_check_access",
            "description": "检查文件修改权限。优先函数级关联测试，否则回退全局覆盖率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要检查的文件路径",
                    },
                    "touched_functions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "计划修改的函数名列表。提供后走函数级检查，更精确。",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "_meta": {"label": "覆盖率报告"},
        "type": "function",
        "function": {
            "name": "evolve_coverage",
            "description": "获取覆盖率报告：各层解锁状态、可修改文件列表。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "_meta": {"label": "进化验证"},
        "type": "function",
        "function": {
            "name": "evolve_validate",
            "description": "运行验证流水线：AST→pytest→覆盖率/lint。每次改代码后必调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "已修改的文件路径列表",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "测试超时秒数，默认 120",
                    },
                    "related_only": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "True=只跑与变更文件相关的测试（通过 import 匹配），秒级反馈。"
                            "False=跑全量测试+完整三阶段验证。"
                        ),
                    },
                },
                "required": ["changed_files"],
            },
        },
    },
    {
        "_meta": {"label": "测试建议"},
        "type": "function",
        "function": {
            "name": "evolve_suggest_tests",
            "description": "获取解锁修改权限的测试建议。支持函数级精确建议（提供 touched_functions）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "想要修改的文件路径",
                    },
                    "touched_functions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "计划修改的函数名列表。提供后走函数级建议。",
                    },
                },
                "required": ["target_file"],
            },
        },
    },
    {
        "_meta": {"label": "进化状态"},
        "type": "function",
        "function": {
            "name": "evolve_status",
            "description": "进化系统状态总览：档案统计 + 覆盖率门禁 + 最近进化记录。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def execute(name: str, args: dict) -> str | None:
    """执行进化系统工具调用。"""
    if name == "evolve_query":
        return _cmd_query(args)
    if name == "evolve_check_access":
        return _cmd_check_access(args)
    if name == "evolve_coverage":
        return _cmd_coverage(args)
    if name == "evolve_validate":
        return _cmd_validate(args)
    if name == "evolve_suggest_tests":
        return _cmd_suggest_tests(args)
    if name == "evolve_status":
        return _cmd_status(args)
    return None


def _cmd_query(args: dict) -> str:
    from ..evolve import query
    keywords = args.get("goal_keywords", [])
    outcome = args.get("outcome")
    tags = args.get("tags")
    limit = args.get("limit", 10)

    if keywords:
        results = query(goal_keywords=keywords, outcome=outcome, tags=tags, limit=limit)
    else:
        results = query(outcome=outcome, tags=tags, limit=limit)

    if not results:
        return "未找到匹配的进化记录。"

    lines = [f"找到 {len(results)} 条进化记录："]
    for r in results:
        goal_short = r.get("goal", "")[:80]
        outcome_icon = {"merged": "✅", "reverted": "❌", "partial": "⚠️"}.get(
            r.get("outcome", ""), "❓")
        lines.append(
            f"  {outcome_icon} [{r.get('timestamp', '')[:16]}] {goal_short}"
        )
        lessons = r.get("lessons_learned", "")
        if lessons:
            lines.append(f"     教训: {lessons[:120]}")
    return "\n".join(lines)


def _cmd_check_access(args: dict) -> str:
    from ..coverage_gate import can_modify
    filepath = args.get("filepath", "")
    touched_functions = args.get("touched_functions")
    if not filepath:
        return "请提供 filepath 参数。"
    allowed, reason = can_modify(filepath, touched_functions=touched_functions)
    if allowed:
        return f"✅ 可以修改: {reason}"
    else:
        return f"⛔ 不能修改: {reason}"


def _cmd_coverage(args: dict) -> str:
    from ..coverage_gate import get_modifiable_files, get_tier_summary
    summary = get_tier_summary()
    files = get_modifiable_files()
    lines = [summary, "", f"可修改文件 ({len(files)} 个):"]
    for f in files[:30]:
        lines.append(f"  - {f}")
    if len(files) > 30:
        lines.append(f"  ... 及其他 {len(files) - 30} 个文件")
    return "\n".join(lines)


def _cmd_validate(args: dict) -> str:
    changed_files = args.get("changed_files", [])
    timeout = args.get("timeout", 120)
    related_only = args.get("related_only", False)

    if not changed_files:
        return "请提供 changed_files 参数。"

    # ── 关联测试快速通道 ──
    if related_only:
        result = _validate_related_only(changed_files, timeout)
        passed = "相关测试全部通过" in result
        return _record_runner_validation(changed_files, result, passed)

    # ── 全量验证 ──
    from ..validate import format_report
    from ..validate import validate as run_validate
    report = run_validate(changed_files, timeout=timeout)
    result = format_report(report)
    if report.overall.value != "pass":
        result += _build_failure_guidance(report, changed_files)
    return _record_runner_validation(changed_files, result, report.overall.value == "pass")


def _record_runner_validation(changed_files: list[str], result: str, passed: bool) -> str:
    """Mirror evolve_validate results into the active evolution runner."""
    try:
        from ..evolution_runner import record_validation_result
        run = record_validation_result(changed_files, result, passed)
    except Exception as e:
        return f"{result}\n\n── Runner 记录 ──\n  记录失败: {e}"
    if run is None:
        return result
    return (
        f"{result}\n\n"
        "── Runner 记录 ──\n"
        f"  run_id: {run.run_id}\n"
        f"  phase: {run.phase}\n"
        f"  validation_passed: {passed}"
    )


def _validate_related_only(changed_files: list[str], timeout: int) -> str:
    """仅运行相关测试的快速验证模式。"""
    from ..validate import pre_commit_checks

    pre = pre_commit_checks(changed_files)

    lines = [
        "═" * 50,
        "关联测试模式 — 仅运行与变更相关的测试",
        f"变更文件: {len(changed_files)} 个",
        "",
    ]
    icon = {"pass": "✅", "fail": "❌", "timeout": "⏱️", "skip": "⏭️"}.get(
        pre.result.value, "❓")
    lines.append(f"  {icon} 静态检查: {pre.details[:200]}")

    if pre.result.value == "fail":
        lines.append("═" * 50)
        return "\n".join(lines)

    related, info = _find_related_tests(changed_files)
    lines.append(f"\n  🔍 {info}")

    if not related:
        lines.append("\n⛔ 变更文件无任何测试保护，拒绝继续。")
        for f in changed_files:
            lines.append(f"     - {Path(f).name}")
        lines.append("═" * 50)
        return "\n".join(lines)

    lines.append(f"\n  相关测试 ({len(related)} 个):")
    for t in related:
        lines.append(f"    - {Path(t).name}")

    lines.append("\n  执行中...")
    passed, output, duration = _run_related_tests(related, timeout)

    if passed:
        lines.append(f"  ✅ 相关测试全部通过 ({duration:.0f}ms)")
        lines.append("")
        lines.append("  💡 提交前用 related_only=False 做全量验证。")
    else:
        lines.append(f"  ❌ 相关测试失败 ({duration:.0f}ms)")
        for line in output.split("\n"):
            stripped = line.strip()
            if any(kw in stripped for kw in (
                "FAILED", "ERROR", "AssertionError",
                "ImportError", "NameError", "TypeError",
            )):
                lines.append(f"     {stripped[:150]}")

    lines.append("═" * 50)
    return "\n".join(lines)


def _extract_error_patterns(test_output: str) -> list[str]:
    """从 pytest 输出中提取可识别的错误模式。"""
    patterns: list[str] = []
    for line in test_output.split("\n"):
        if "ImportError" in line or "ModuleNotFoundError" in line:
            patterns.append(f"导入错误 — 检查依赖或 import 路径: {line.strip()[:120]}")
        elif "AssertionError" in line:
            patterns.append(f"断言失败 — 预期与实际值不符: {line.strip()[:120]}")
        elif "AttributeError" in line:
            patterns.append(f"属性错误 — 对象缺少方法或参数: {line.strip()[:120]}")
        elif "NameError" in line:
            patterns.append(f"名称错误 — 变量或函数未定义: {line.strip()[:120]}")
        elif "TypeError" in line:
            patterns.append(f"类型错误 — 参数或返回值类型不匹配: {line.strip()[:120]}")
        elif "SyntaxError" in line:
            patterns.append(f"语法错误 — 代码无法解析: {line.strip()[:120]}")
        elif "FAILED" in line and "::" in line:
            patterns.append(f"测试失败 — {line.strip()[:150]}")
    return patterns[:5]


def _build_failure_guidance(report, changed_files: list[str]) -> str:
    """测试失败时自动生成修复指导。"""
    parts: list[str] = []
    patterns = _extract_error_patterns(getattr(report, "test_output", ""))
    if patterns:
        parts.append("\n── 检测到的错误模式 ──")
        for i, p in enumerate(patterns, 1):
            parts.append(f"  {i}. {p}")

    try:
        from ..evolve import find_similar
        keywords = [Path(f).stem for f in changed_files[:2]]
        if patterns:
            keywords.append(patterns[0][:50])
        similar = find_similar(keywords, limit=3)
        if similar:
            parts.append("\n── 历史上类似修复（来自进化档案）──")
            for rec in similar:
                outcome = rec.get("outcome", "?")
                goal = rec.get("goal", "")[:100]
                parts.append(f"  [{outcome}] {goal}")
    except Exception:
        pass

    parts.append("\n── 建议下一步 ──")
    parts.append("  1. 根据错误模式定位问题文件")
    parts.append("  2. 调用 think 分析根因而非症状")
    parts.append("  3. 先补测试再改代码")
    parts.append("  4. 修复后重新调用 evolve_validate 验证")
    return "\n".join(parts)


def _cmd_suggest_tests(args: dict) -> str:
    from ..coverage_gate import suggest_tests_to_unlock
    target = args.get("target_file", "")
    touched_functions = args.get("touched_functions")
    if not target:
        return "请提供 target_file 参数。"
    return suggest_tests_to_unlock(target, touched_functions=touched_functions)


def _cmd_status(args: dict) -> str:
    from ..coverage_gate import get_tier_summary
    from ..evolution_runner import describe_active_evolution_run
    from ..evolve import get_last_n, get_stats

    stats = get_stats()
    tier = get_tier_summary()
    recent = get_last_n(3)

    lines = [
        "═══════════════════════════════════",
        "       自进化系统状态",
        "═══════════════════════════════════",
        "",
        "── 进化档案 ──",
    ]
    if stats.get("total_attempts", 0) > 0:
        lines.append(f"  总尝试: {stats['total_attempts']}")
        lines.append(f"  成功: {stats.get('merged', 0)}  |  "
                     f"回滚: {stats.get('reverted', 0)}  |  "
                     f"成功率: {stats.get('success_rate', 0)}%")
    else:
        lines.append("  暂无进化记录")

    lines.extend(["", tier])
    lines.extend(["", "── Active Runner ──", describe_active_evolution_run()])

    if recent:
        lines.extend(["", "── 最近进化 ──"])
        for r in recent:
            icon = {"merged": "✅", "reverted": "❌", "partial": "⚠️"}.get(
                r.get("outcome", ""), "❓")
            lines.append(f"  {icon} {r.get('goal', '')[:80]}")

    return "\n".join(lines)
