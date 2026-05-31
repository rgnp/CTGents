"""子代理 — 独立上下文执行任务，不污染主 Agent 上下文。

使用 CacheContext 复用前缀缓存：system prompt 不可变，各轮增量 token 仅需计费一次。
"""

import json
import logging
from types import SimpleNamespace

from ..cache_context import CacheContext

logger = logging.getLogger(__name__)

# ── 工具定义 ──

TOOLS_SUBAGENT = [
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": (
                "创建一个子代理独立执行任务。子代理有自己的独立上下文 + 前缀缓存，"
                "只读工具（读文件、搜代码、查网络），完成后返回结果摘要。"
                "适合：搜索代码、分析问题、调研文档。"
                "不适合：修改文件、运行命令、装插件。"
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

# ── 只读工具白名单 ──
_READONLY_TOOLS = frozenset({
    "read_file", "read_file_lines", "grep_code", "list_files", "count_lines",
    "scan_project", "git_status", "git_diff", "git_log", "git_branch",
    "search_web", "read_page", "think", "rag_query", "rag_status",
    "discover", "check_project", "docs_sync_check",
})

MAX_TURNS = 5                     # 最多 LLM 调用轮数（小任务通常 2-3 轮）
TOOL_RESULT_MAX_CHARS = 2000      # 单条工具结果截断


def execute(name: str, args: dict) -> str | None:
    if name == "subagent":
        return _run_subagent(args.get("task", ""))
    return None


def _build_prefix() -> list[dict]:
    """构建子代理不可变前缀（系统提示 + 工具可用性）。"""
    return [
        {
            "role": "system",
            "content": (
                "你是子代理，独立完成用户交代的任务。\n\n"
                "规则：\n"
                "- 用工具查找信息（读文件、搜代码、查网络）\n"
                "- 完成后输出简洁结论，不说过程\n"
                "- 不修改文件、不执行命令"
            ),
        },
    ]


def _run_subagent(task: str) -> str:
    """创建子代理，使用 CacheContext 享受前缀缓存。"""
    from ..llm import _invoke_llm, auto_select_model
    from ..tools import execute_tool  # noqa: F811

    backend = auto_select_model(task)

    ctx = CacheContext(prefix_msgs=_build_prefix())
    ctx.log.append({"role": "user", "content": task})

    # ── 工具结果去重窗口（子代理独立，避免重复读同一文件） ──
    seen_results: set[str] = set()

    for turn in range(1, MAX_TURNS + 1):
        api_msgs = ctx.send()
        try:
            content, tool_calls = _invoke_llm(backend, api_msgs, lambda _: None)
        except Exception as e:
            return f"子代理出错: {e}"

        if not tool_calls:
            return content or "（无结果）"

        # 记录 assistant 消息到 log
        ctx.log.append({
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
                    {"error": f"子代理不可用: {tc_name}"}, ensure_ascii=False,
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

                # 截断长结果
                if len(result) > TOOL_RESULT_MAX_CHARS:
                    result = result[:TOOL_RESULT_MAX_CHARS] + (
                        f"\n...（共 {len(result)} 字符，已截断）"
                    )

                # 子代理去重：相同结果只保留一次
                result_key = f"{tc_name}:{result[:80]}"
                if result_key in seen_results:
                    result = result[:300] + "\n（重复结果，已省略细节）"
                else:
                    seen_results.add(result_key)

            ctx.log.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })

    return "（子代理未在轮数内完成）"
