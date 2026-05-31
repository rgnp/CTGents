"""子代理 — 独立上下文执行任务，不污染主 Agent 上下文。

子代理有自己独立的对话，只读工具权限，完成后返回结果摘要。
"""

import json
import logging
from types import SimpleNamespace

logger = logging.getLogger(__name__)

# ── 工具定义 ──

TOOLS_SUBAGENT = [
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": (
                "创建一个子代理独立执行任务。子代理有自己的独立上下文，"
                "可以用只读工具（读文件、搜代码、查网络），完成后返回结果摘要。"
                "适合：搜索代码、分析问题、调研文档等任务。"
                "不适合：修改文件、运行命令、安装插件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "子代理的任务描述，写清楚要做什么、找到什么信息",
                    },
                },
                "required": ["task"],
            },
        },
    },
]

# ── 子代理只读工具白名单 ──
_READONLY_TOOLS = frozenset({
    "read_file", "read_file_lines", "grep_code", "list_files", "count_lines",
    "scan_project", "git_status", "git_diff", "git_log", "git_branch",
    "search_web", "read_page", "think", "rag_query", "rag_status",
    "discover", "check_project", "docs_sync_check",
})

MAX_TURNS = 6  # 子代理最多 LLM 调用轮数


def execute(name: str, args: dict) -> str | None:
    """工具执行入口。"""
    if name == "subagent":
        return _run_subagent(args.get("task", ""))
    return None


def _run_subagent(task: str) -> str:
    """创建子代理并执行任务。"""
    from ..llm import _invoke_llm, auto_select_model
    from ..tools import execute_tool  # noqa: F811 — 运行时导入避免循环依赖

    backend = auto_select_model(task)

    system_prompt = (
        "你是子代理，独立完成用户交代的任务。\n\n"
        "规则：\n"
        "- 使用提供的工具查找信息（读文件、搜代码、查网络）\n"
        "- 完成后输出结果摘要，只说结论不用过程\n"
        "- 你不能修改任何文件或执行命令\n"
        "- 如果工具不够用，就基于已有信息分析"
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    for turn in range(1, MAX_TURNS + 1):
        try:
            content, tool_calls = _invoke_llm(backend, messages, lambda _: None)
        except Exception as e:
            return f"子代理出错: {e}"

        # 没有工具调用 → 返回最终结果
        if not tool_calls:
            return content or "（无结果）"

        # 记录 assistant 消息
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        # 执行工具
        for tc in tool_calls:
            tc_name = tc.get("function", {}).get("name", "")
            tc_args_raw = tc.get("function", {}).get("arguments", "{}")

            if tc_name not in _READONLY_TOOLS:
                result = json.dumps(
                    {"error": f"子代理不可用: {tc_name}（仅只读工具）"},
                    ensure_ascii=False,
                )
            else:
                try:
                    tc_args = json.loads(tc_args_raw)
                except (json.JSONDecodeError, ValueError):
                    tc_args = {}

                tc_obj = SimpleNamespace(
                    function=SimpleNamespace(
                        name=tc_name,
                        arguments=json.dumps(tc_args, ensure_ascii=False),
                    )
                )
                result = execute_tool(tc_obj)
                if len(result) > 3000:
                    result = result[:3000] + f"\n...（共 {len(result)} 字符，已截断）"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    return "（子代理到达最大轮数，未完成任务）"
