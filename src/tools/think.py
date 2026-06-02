TOOLS_THINK = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "策略规划工具。复杂任务时拆解子问题、规划步骤；收到信息后评估完整性、决定下一步。思考内容持久化在上下文中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "思考内容",
                    }
                },
                "required": ["thought"],
            },
        },
    },
]


def think(thought: str) -> str:
    return ""


def execute(name: str, args: dict) -> str | None:
    if name == "think":
        return think(args["thought"])
    return None
