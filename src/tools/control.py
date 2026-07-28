"""循环控制工具：agent 显式喊停，取代"不调工具=本轮结束"的隐式信号。


# psyche 加载/卸载工具名（由 _handle_tool_results 识别并实际执行）
PSYCHE_TOOLS = frozenset({"load_psyche", "unload_psyche"})
SKILL_TOOLS = frozenset({"activate_skill"})


`task_done` / `need_user` 走原生 function-calling（不另搞 JSON 协议=不降工具调用稳定性）。
`llm.run_conversation` 检测到本批调用了控制工具，就把名字+附带文本写进 `ctx.control_signal`
并结束本轮；长任务续跑（`task_loop.run_task_continuation`）据此判断"停"（need_user/
task_done）还是"继续"（仍有未完成步骤）。本模块自身无副作用——控制效果在 run_conversation。

execute 对外来工具名必须返回 None（派发链契约，见 tools/__init__.execute_tool）。
"""
from __future__ import annotations

# 控制信号工具名——llm.run_conversation / task_loop 据此识别显式停止信号。单一真相源。
CONTROL_TOOLS = frozenset({"task_done", "need_user"})

TOOLS_CONTROL = [
    {
        "_meta": {"group": "core", "label": "任务完成", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "task_done",
            "description": (
                "显式声明当前任务已完成、可以停下。长任务所有步骤做完"
                "（current.md 全 [x]）时调用。不在回复里说'做完了'——"
                "那不被当作停止信号。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "一句话总结完成了什么"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "需要拍板", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "need_user",
            "description": (
                "需要用户输入/决策才能继续时调用。用于方案要用户选、"
                "缺信息、要拍板的岔路。这是暂停的正式信号——不在回复里说，"
                "避免被续跑覆盖。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "要问用户什么（具体、可回答）"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "加载Psyche", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "load_psyche",
            "description": (
                "发现具体认知缺口时，自主加载 task-scope Psyche。"
                "不能因为关键词加载；reason 必须说明不加载会漏掉什么判断维度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "psyche 名称，如 learning-method"},
                    "reason": {
                        "type": "string",
                        "description": "具体认知缺口，以及该 Psyche 会改变什么判断",
                    },
                },
                "required": ["name", "reason"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "Psyche目录", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "psyche_catalog",
            "description": (
                "显式查询 Psyche 能力目录，用于发现能补当前认知缺口的框架。"
                "只查询，不自动加载。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "可选：要补的判断维度"},
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "卸载Psyche", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "unload_psyche",
            "description": "卸载已加载的 psyche，释放上下文空间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要卸载的 psyche 名称"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "激活Skill", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "activate_skill",
            "description": (
                "由当前 active Psyche 调用其 manifest 声明的 Skill。"
                "owner Psyche 未激活或未声明该 Skill 时会被机械拒绝。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill 名称"},
                    "axes": {
                        "type": "object",
                        "description": "可选轴选择，如 depth/paper_type/domain",
                        "additionalProperties": {"type": "string"},
                    },
                    "reason": {"type": "string", "description": "为什么当前 Psyche 需要此流程"},
                },
                "required": ["name", "reason"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "更新计划", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "重规划当前任务：改写 tasks/current.md 的步骤清单。"
                "当任务推进中发现原计划不再匹配、步骤顺序不对、需新增或删除"
                "步骤时调用。保留 # 目标锚点，重写步骤清单。"
                "注意：这不是标记完成，这是重写计划本身。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "完整的 current.md 新内容，必须包含 # 目标锚点 行和步骤清单",
                    },
                },
                "required": ["plan"],
            },
        },
    },
    {
        "_meta": {"label": "工具发现", "group": "core", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "search_tools",
            "description": (
                "渐进式工具发现：浏览工具生态、搜索匹配的工具、加载可选组。"
                "不传参数=列出全部工具组及状态；query=搜索匹配；load=加载可选组"
                "（如 research/git/memory-mutate/files-mutate）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词（匹配工具名和描述），不传=列出所有组",
                    },
                    "load": {
                        "type": "string",
                        "description": "要加载的可选工具组名（research/git/memory-mutate/files-mutate）",
                    },
                },
                "required": [],
            },
        },
    },
]

def execute(name: str, args: dict) -> str | None:
    if name == "task_done":
        summary = (args.get("summary") or "").strip()
        try:
            from ..tasks import _BLOCKED_MARKERS, _UNFINISHED_MARKERS
            from ..tasks import archive_current_if_accepted as _archive_current_if_accepted
            from ..tasks import parse_task_spec as _parse_task_spec
            from ..tasks import read_current as _read_current
            current = _read_current()
            if "[ ] 质量自检" in current and "[x] 质量自检" not in current:
                return (
                    "❌ task_done 被拦截：current.md 中有未完成的 `[ ] 质量自检` 步骤。"
                    "完成任务前，请先对照「三问质量方法论」做自检，"
                    "完成后把该步标为 `[x] 质量自检`。"
                    "（如果这是一个不需要自检的小任务，先把该步删掉或标 [x]。）"
                )
            task_spec = _parse_task_spec(current)
            if task_spec.acceptance_declared and any(
                marker in current for marker in _UNFINISHED_MARKERS + _BLOCKED_MARKERS
            ):
                return (
                    "❌ task_done 被验收合同拦截：任务正文仍有未完成或阻塞步骤。"
                    "请先完成步骤，再执行结构化验收。"
                )
            # 全 [x] 自动归档——不用等 agent 单独调 archive
            if current.strip() and not any(m in current for m in _UNFINISHED_MARKERS + _BLOCKED_MARKERS):
                archive_msg = _archive_current_if_accepted()
                if archive_msg.startswith("❌"):
                    return (
                        "❌ task_done 被验收合同拦截。请按失败原因修复后再次调用。\n"
                        f"{archive_msg}"
                    )
                return f"[任务完成信号] {summary}\n{archive_msg}"
        except Exception:
            pass  # 读不到 current.md 时放行（容错，不阻塞正常 task_done）
        return f"[任务完成信号] {summary}"
    if name == "need_user":
        return f"[需要用户拍板] {(args.get('question') or '').strip()}"
    if name == "update_plan":
        try:
            from ..tasks import update_plan as _update_plan
            return _update_plan((args.get("plan") or "").strip())
        except ImportError:
            from src.tasks import update_plan as _update_plan
            return _update_plan((args.get("plan") or "").strip())
    if name == "load_psyche":
        target = (args.get("name") or "").strip()
        if not target:
            return "❌ psyche 名称不能为空。"
        reason = (args.get("reason") or "").strip()
        if not reason:
            return "❌ Agent 自主加载 Psyche 必须说明具体认知缺口。"
        return f"[加载 psyche: {target} | reason: {reason}]"
    if name == "unload_psyche":
        target = (args.get("name") or "").strip()
        if not target:
            return "❌ psyche 名称不能为空。"
        return f"[卸载 psyche: {target}]"
    if name == "psyche_catalog":
        from ..psyche_catalog import PsycheCatalogError, catalog_text, load_catalog

        try:
            return catalog_text(load_catalog(), (args.get("query") or "").strip())
        except PsycheCatalogError as exc:
            return f"❌ Psyche Catalog 无效: {exc}"
    if name == "activate_skill":
        target = (args.get("name") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not target:
            return "❌ Skill 名称不能为空。"
        if not reason:
            return "❌ Psyche 调用 Skill 必须说明用途。"
        return f"[请求激活 Skill: {target} | reason: {reason}]"
    if name == "search_tools":
        return _handle_search_tools(args)
    return None


def _handle_search_tools(args: dict) -> str:
    """搜索/浏览/加载工具组。"""
    from . import (
        _OPTIONAL_GROUPS,
        _TOOL_SOURCES,
        _enabled_groups,
        enable_tool_group,
    )

    load_group = (args.get("load") or "").strip()
    query = (args.get("query") or "").strip()

    if load_group:
        if load_group not in _OPTIONAL_GROUPS:
            return (
                f"❌ 未知工具组 '{load_group}'。可选：{', '.join(sorted(_OPTIONAL_GROUPS))}"
            )
        return enable_tool_group(load_group)

    # Load all tools with _meta
    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)

    # Group
    groups: dict[str, list[dict]] = {}
    for t in tools:
        grp = t.get("_meta", {}).get("group", "ungrouped")
        groups.setdefault(grp, []).append(t)

    if query:
        q = query.lower()
        hits = []
        for grp in sorted(groups):
            for t in groups[grp]:
                fn = t["function"]
                if q in fn["name"].lower() or q in fn["description"].lower():
                    loaded = "✅" if grp not in _OPTIONAL_GROUPS or grp in _enabled_groups else "⏸️"
                    hits.append(f"  {loaded} [{grp}] {fn['name']} — {fn['description'][:80]}")
        if not hits:
            return f"没有匹配 '{query}' 的工具。"
        return f"搜索 '{query}' 匹配 {len(hits)} 个工具：\n" + "\n".join(hits[:30])

    lines = ["工具组（渐进式发现）：\n"]
    for grp in sorted(groups):
        loaded = grp not in _OPTIONAL_GROUPS or grp in _enabled_groups
        icon = "✅" if loaded else "⏸️"
        lines.append(f"\n## {grp} — {icon} ({len(groups[grp])}个)")
        for t in sorted(groups[grp], key=lambda x: x["function"]["name"]):
            fn = t["function"]
            lines.append(f"  • {fn['name']:<25} {fn['description'][:70]}")
    lines.append(
        "\n───\n加载可选组：search_tools(load=\"research\")。"
        "各组：research=论文文献, rag=知识库索引, repo=仓库, "
        "git=版本控制, memory-mutate=记忆写入, files-mutate=文件删除"
    )
    return "\n".join(lines)
