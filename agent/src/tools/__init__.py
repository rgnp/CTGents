import json

from openai.types.chat import ChatCompletionMessageToolCall

from .tokens import truncate_to_budget, estimate_tokens, count_messages_tokens
from .web import TOOLS_WEB, search_web, read_page
from .file import TOOLS_FILE, read_file, write_file, list_files
from .exec import TOOLS_EXEC, run_python

TOOLS = TOOLS_WEB + TOOLS_FILE + TOOLS_EXEC


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行单个工具调用，返回原始结果（截断由调用方处理）。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    if name == "search_web":
        return search_web(args["query"])

    if name == "read_page":
        return read_page(args["url"])

    if name == "read_file":
        return read_file(args["path"])

    if name == "write_file":
        return write_file(args["path"], args["content"])

    if name == "list_files":
        return list_files(args.get("path"))

    if name == "run_python":
        return run_python(args["code"])

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
