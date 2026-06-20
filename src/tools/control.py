"""循环控制工具：agent 显式喊停，取代"不调工具=本轮结束"的隐式信号。

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
                "显式声明当前任务/本轮目标已完成、可以停下。长任务里：所有步骤都做完"
                "（current.md 全 [x]）时调用，循环会归档任务并停止。"
                "这是结束的正式信号——别只在回复里说'做完了'，那不会被当作停止。"
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
                "需要用户输入/决策才能继续时调用，循环会停下并把问题交给用户。"
                "用于：方案要用户选、缺关键信息、遇到要用户拍板的岔路。"
                "这是暂停的正式信号——别只在回复里问，那不会被当作停止、可能被自动续跑覆盖。"
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
]


def execute(name: str, args: dict) -> str | None:
    if name == "task_done":
        return f"[任务完成信号] {(args.get('summary') or '').strip()}"
    if name == "need_user":
        return f"[需要用户拍板] {(args.get('question') or '').strip()}"
    return None
