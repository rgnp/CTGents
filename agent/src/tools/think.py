TOOLS_THINK = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "记录思考过程，写入对话历史供后续步骤参考。"
                "用于：拆解复杂任务、规划步骤、评估中间结果、调整策略。"
                "内容是自由文本，想什么写什么。"
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
    return "已记录思考"
