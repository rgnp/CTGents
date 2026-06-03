"""进化系统工具 — 暴露给 LLM 的自进化能力。

工具列表：
  evolve_query        — 查询进化档案，了解过去的自修改尝试
  evolve_check_access — 检查是否有权限修改指定文件
  evolve_coverage     — 获取当前测试覆盖率报告
  evolve_validate     — 运行验证流水线（静态检查→沙箱测试→后检查）
  evolve_suggest_tests— 获取解锁修改权限的测试建议
"""

import json

TOOLS_EVOLVE: list[dict] = [
    {
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
        "type": "function",
        "function": {
            "name": "evolve_check_access",
            "description": (
                "检查当前是否有权限修改指定文件。"
                "返回允许/拒绝及原因。如果没有权限，会告诉你需要多少测试覆盖率才能解锁。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要检查的文件路径",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
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
        "type": "function",
        "function": {
            "name": "evolve_validate",
            "description": (
                "运行验证流水线：静态检查（AST+import+lint）→ 沙箱测试（pytest）"
                "→ 后检查（覆盖率不降+无新增lint错误）。"
                "在代码修改完成后、最终合入前调用。超时默认 120 秒。"
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
        "type": "function",
        "function": {
            "name": "evolve_suggest_tests",
            "description": (
                "获取建议：需要添加哪些测试才能解锁对目标文件的修改权限。"
                "当 evolve_check_access 返回拒绝时调用，了解需要做什么才能解锁。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "想要修改的文件路径",
                    },
                },
                "required": ["target_file"],
            },
        },
    },
    {
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
    if not filepath:
        return "请提供 filepath 参数。"
    allowed, reason = can_modify(filepath)
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
    return format_report(report)


def _cmd_suggest_tests(args: dict) -> str:
    from ..coverage_gate import suggest_tests_to_unlock
    target = args.get("target_file", "")
    if not target:
        return "请提供 target_file 参数。"
    return suggest_tests_to_unlock(target)


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
