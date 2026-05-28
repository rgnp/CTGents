import json

from openai.types.chat import ChatCompletionMessageToolCall

from .tokens import truncate_to_budget, estimate_tokens, count_messages_tokens
from .web import TOOLS_WEB, search_web, read_page
from .file import TOOLS_FILE, read_file, write_file, list_files, delete_file
from .exec import TOOLS_EXEC, run_python
from .code import TOOLS_CODE, grep_code
from .think import TOOLS_THINK, think
from .plugin_mgr import discover_plugins, install_plugin, list_plugins, execute_plugin

TOOLS_BUILTIN = TOOLS_WEB + TOOLS_FILE + TOOLS_EXEC + TOOLS_CODE + TOOLS_THINK + [
    {
        "type": "function",
        "function": {
            "name": "install_plugin",
            "description": (
                "安装新插件——写入 Python 代码到 plugins/ 目录并热加载。"
                "当你从网上学会一种新能力后，用这个工具为自己安装。"
                "代码中必须定义 TOOLS（工具描述列表）和 execute(name, args) 函数。"
                "参考：在 list_plugins 的描述里包含插件编写规范。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "插件名称（如 data_viz、text_stats），不含扩展名",
                    },
                    "code": {
                        "type": "string",
                        "description": "完整的 Python 插件代码",
                    },
                },
                "required": ["name", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_plugins",
            "description": (
                "列出已安装的插件。首次使用时调用，查看当前有哪些能力扩展。"
                "如果没有任何插件，你可以上网搜索学习后用 install_plugin 安装。"
                "\n插件编写规范：每个插件是一个 Python 文件，需包含：\n"
                "TOOLS = [{type: function, function: {name, description, parameters}}]\n"
                "def execute(name, args): ...  # 根据 name 分发并返回结果字符串\n"
                "可 import 任意标准库和已安装的第三方库。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_plugin_tools: list[dict] = []


def _load_plugins() -> None:
    global _plugin_tools
    _plugin_tools = discover_plugins()


def get_tools() -> list[dict]:
    """返回完整工具列表（内置 + 已加载插件），每次调用重建以包含热加载的插件。"""
    return TOOLS_BUILTIN + _plugin_tools


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行单个工具调用。先查插件，再查内置。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    # 内置——插件管理
    if name == "install_plugin":
        result = install_plugin(args["name"], args["code"])
        _load_plugins()  # 重新扫描，使新插件立即可用
        return result

    if name == "list_plugins":
        return list_plugins()

    # 插件
    result = execute_plugin(name, args)
    if result is not None:
        return result

    # 内置
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
    if name == "delete_file":
        return delete_file(args["path"])
    if name == "grep_code":
        return grep_code(args["pattern"], args.get("path"))
    if name == "think":
        return think(args["thought"])

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


# 启动时加载已有插件
_load_plugins()
