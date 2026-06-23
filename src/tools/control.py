"""循环控制工具：agent 显式喊停，取代"不调工具=本轮结束"的隐式信号。


# psyche 加载/卸载工具名（由 _handle_tool_results 识别并实际执行）
PSYCHE_TOOLS = frozenset({"load_psyche", "unload_psyche"})


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
        "_meta": {"label": "任务完成", "no_dedup": True},
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
        "_meta": {"label": "需要拍板", "no_dedup": True},
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
        "_meta": {"label": "加载Psyche", "no_dedup": True},
        "type": "function",
        "function": {
            "name": "load_psyche",
            "description": "加载 psyche（认知框架），加载后对话遵循该 psyche 的准则。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "psyche 名称，如 learning-method"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "_meta": {"label": "卸载Psyche", "no_dedup": True},
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
]


def execute(name: str, args: dict) -> str | None:
    if name == "task_done":
        return f"[任务完成信号] {(args.get('summary') or '').strip()}"
    if name == "need_user":
        return f"[需要用户拍板] {(args.get('question') or '').strip()}"
    if name == "load_psyche":
        target = (args.get("name") or "").strip()
        if not target:
            return "❌ psyche 名称不能为空。"
        return f"[加载 psyche: {target}]"
    if name == "unload_psyche":
        target = (args.get("name") or "").strip()
        if not target:
            return "❌ psyche 名称不能为空。"
        return f"[卸载 psyche: {target}]"
    return None
