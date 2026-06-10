TOOLS_THINK = [
    {
        "_meta": {"label": "思考", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "思考工具：两种模式 — ①策略规划（拆解任务、评估进展、决定下一步）；"
                "②推理检查点（展开中间步骤、暴露前提、让假前提在到达输出前被抓住）。"
                "简单任务不需要。"
            ),
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
