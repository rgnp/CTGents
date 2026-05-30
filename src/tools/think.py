TOOLS_THINK = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "你的策略规划工具，在以下时机调用会显著提升表现："
                "1) 接到复杂任务时——先拆解子问题、规划搜索顺序，避免盲目搜索浪费 token；"
                "2) 读完网页后——评估已有信息是否够用，还缺什么，决定下一步；"
                "3) 发现新线索时——调整计划，补充新的子问题。"
                "思考内容持久化在上下文中，调研越长价值越大。"
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
