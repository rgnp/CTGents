TOOLS_THINK = [
    {
        "_meta": {"label": "思考", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "think",
            "description": "策略规划：拆解任务、评估进展、决定下一步。",
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
