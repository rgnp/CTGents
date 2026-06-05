"""进化系统工具 — 暴露给 LLM 的自进化能力。

工具列表：
  evolve_query        — 查询进化档案，了解过去的自修改尝试
  evolve_check_access — 检查是否有权限修改指定文件
  evolve_coverage     — 获取当前测试覆盖率报告
  evolve_validate     — 运行验证流水线（静态检查→沙箱测试→后检查）
  evolve_suggest_tests— 获取解锁修改权限的测试建议
"""

import json
from pathlib import Path
TOOLS_EVOLVE: list[dict] = [
    {
        "_meta": {"label": "进化查询"},
        "type": "function",
        "function": {
            "name": "evolve_query",
            "description": (
                "查询进化档案——搜索过去的自修改尝试。"
                "在做任何自修改之前先调用，了解什么方法有效、什么会导致失败。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标关键词，用于搜索相似的进化记录",
                    },
                    "outcome": {
                        "type": "string",
                        "enum": ["merged", "reverted", "partial"],
                        "description": "按结果筛选。merged=成功的修改，reverted=回滚的修改",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按标签筛选，如 performance、bugfix、refactor",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "description": "最多返回多少条",
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
            "description": (
                "检查当前是否有权限修改指定文件。优先进行函数级关联测试检查："
                "如果要改的函数有测试保护，直接放行；没覆盖则精确列出需补测试的函数名。"
                "不提供 touched_functions 时回退到全局覆盖率 tier 检查。"
            ),
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
                        "description": (
                            "计划修改的函数/方法名列表（可选但强烈推荐）。"
                            "提供后进行函数级关联测试检查——只查这些函数是否有测试覆盖。"
                            "比全局覆盖率检查更精确、更容易通过。"
                        ),
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
            "description": (
                "获取当前测试覆盖率报告。包含各层级解锁状态、可修改文件列表、"
                "覆盖率差距。在规划自修改时调用，了解哪些文件可以动、哪些需要先加测试。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "_meta": {"label": "进化验证"},
        "type": "function",
        "function": {
            "name": "evolve_validate",
            "description": (
                "运行验证流水线：静态检查（AST+import+lint）→ 沙箱测试（pytest）"
                "→ 后检查（覆盖率不降+无新增lint错误）。"
                "⚠️ 每次修改代码后必须调用此工具验证，不要手动让用户去测。超时默认 120 秒。"
            ),
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
                        "default": 120,
                        "description": "测试超时秒数",
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
            "description": (
                "获取建议：需要添加哪些测试才能解锁对目标文件的修改权限。"
                "支持函数级精确建议：提供 touched_functions 后，只列出未覆盖的函数及行号。"
                "当 evolve_check_access 返回拒绝时调用，了解需要做什么才能解锁。"
            ),
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
                        "description": (
                            "计划修改的函数名列表（可选）。提供后给出函数级精确建议，"
                            "包括每个函数的行号和覆盖状态。"
                        ),
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
    from ..evolve import query, find_similar
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
    from ..coverage_gate import get_tier_summary, get_modifiable_files
    summary = get_tier_summary()
    files = get_modifiable_files()
    lines = [summary, "", f"可修改文件 ({len(files)} 个):"]
    for f in files[:30]:
        lines.append(f"  - {f}")
    if len(files) > 30:
        lines.append(f"  ... 及其他 {len(files) - 30} 个文件")
    return "\n".join(lines)


def _cmd_validate(args: dict) -> str:
    from ..validate import validate as run_validate, format_report
    changed_files = args.get("changed_files", [])
    timeout = args.get("timeout", 120)
    if not changed_files:
        return "请提供 changed_files 参数。"
    report = run_validate(changed_files, timeout=timeout)
    result = format_report(report)
    if report.overall.value != "pass":
        result += _build_failure_guidance(report, changed_files)
    return result


def _extract_error_patterns(test_output: str) -> list[str]:
    """从 pytest 输出中提取可识别的错误模式。"""
    patterns: list[str] = []
    lines = test_output.split("\n")

    for line in lines:
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

    return patterns[:5]  # 最多 5 条，避免过多


def _build_failure_guidance(report, changed_files: list[str]) -> str:
    """测试失败时自动生成修复指导：查询历史教训 + 建议下一步。"""
    parts: list[str] = []

    # 提取错误模式
    patterns = _extract_error_patterns(getattr(report, "test_output", ""))
    if patterns:
        parts.append("\n── 检测到的错误模式 ──")
        for i, p in enumerate(patterns, 1):
            parts.append(f"  {i}. {p}")

    # 查询历史进化记录中类似错误的修复方案
    try:
        from ..evolve import find_similar
        # 用第一个错误模式或变更文件名作为关键词搜索
        keywords = []
        for f in changed_files[:2]:
            keywords.append(Path(f).stem)
        if patterns:
            keywords.append(patterns[0][:50])

        if keywords:
            similar = find_similar(keywords, limit=3)
            if similar:
                parts.append("\n── 历史上类似修复（来自进化档案）──")
                for rec, score in similar:
                    outcome = rec.get("outcome", "?")
                    goal = rec.get("goal", "")[:100]
                    parts.append(f"  [{outcome}] {goal} (相似度: {score:.2f})")
    except Exception:
        pass

    # 建议下一步
    parts.append("\n── 建议下一步 ──")
    parts.append("  1. 根据错误模式定位问题文件")
    parts.append("  2. 调用 think 分析根因而非症状")
    parts.append("  3. 先补测试再改代码")
    parts.append("  4. 修复后重新调用 evolve_validate 验证")
    parts.append(f"  5. 如需研究类似问题的解决方案，调用 search_web 搜索")

    return "\n".join(parts)


def _cmd_suggest_tests(args: dict) -> str:
    from ..coverage_gate import suggest_tests_to_unlock
    target = args.get("target_file", "")
    touched_functions = args.get("touched_functions")
    if not target:
        return "请提供 target_file 参数。"
    return suggest_tests_to_unlock(target, touched_functions=touched_functions)


def _cmd_status(args: dict) -> str:
    from ..evolve import get_stats, get_last_n
    from ..coverage_gate import get_tier_summary

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

    if recent:
        lines.extend(["", "── 最近进化 ──"])
        for r in recent:
            icon = {"merged": "✅", "reverted": "❌", "partial": "⚠️"}.get(
                r.get("outcome", ""), "❓")
            lines.append(f"  {icon} {r.get('goal', '')[:80]}")

    return "\n".join(lines)
