"""钉板工具:agent 主动 pin / unpin 本会话"绝不能忘"的决定/约束。

内存态与封顶逻辑在 src/session_pins.py(顶层 core 关注点);此处仅是工具壳。
execute 对外来工具名必须返回 None(派发链契约,见 tools/__init__.execute_tool)。
"""

from __future__ import annotations

from ..session_pins import add_pin, remove_pin

TOOLS_PIN = [
    {
        "_meta": {"label": "钉住", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "pin",
            "description": (
                "把本会话'绝不能忘'的决定/约束钉在上下文尾部,防止长会话里漂移/被遗忘。"
                "一句话、要短(超长会被截断)。长内容或跨会话知识请改用 remember。"
                "durable=true 的 pin 会在会话结束时转存进记忆。决定失效后用 unpin 取下。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "一句话决定/约束"},
                    "durable": {
                        "type": "boolean",
                        "description": "是否值得跨会话保留(会话结束转存 memory),默认 false",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "_meta": {"label": "取下", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "unpin",
            "description": "取下一条已钉的内容(决定已失效/完成时)。按文本子串匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "要取下的 pin 文本(可子串)"},
                },
                "required": ["content"],
            },
        },
    },
]


def execute(name: str, args: dict) -> str | None:
    if name == "pin":
        return add_pin(args["content"], bool(args.get("durable", False)))
    if name == "unpin":
        return remove_pin(args["content"])
    return None
