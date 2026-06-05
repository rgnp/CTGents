"""代码静态分析工具 — 死代码检测 + 代码坏味道 + 复杂度分析。

薄包装：复用 analyzer.py 的 ProjectAnalyzer，暴露为 LLM 可调用工具。
"""

from __future__ import annotations

from pathlib import Path

from .analyzer import ProjectAnalyzer

TOOLS_ANALYZER: list[dict] = [
    {
        "_meta": {
            "label": "代码分析",
            "parallel_safe": True,
        },
        "type": "function",
        "function": {
            "name": "analyze_code",
            "description": (
                "静态分析项目代码：检测死代码、圈复杂度超标、嵌套过深、"
                "裸 except、可变默认参数等代码坏味道。"
                "修改代码前调用，确认改动区域不存在已知问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "include_tests": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否同时分析 tests/ 目录",
                    },
                },
                "required": [],
            },
        },
    },
]


def execute(name: str, args: dict) -> str | None:
    if name == "analyze_code":
        return _do_analyze(args)
    return None


def _do_analyze(args: dict) -> str:
    include_tests = args.get("include_tests", False)
    root = Path(__file__).resolve().parent.parent.parent

    analyzer = ProjectAnalyzer(root)
    report = analyzer.analyze(include_tests=include_tests)
    return analyzer.format_report(report)
