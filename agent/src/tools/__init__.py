import json

from openai.types.chat import ChatCompletionMessageToolCall

from .tokens import truncate_to_budget, estimate_tokens, count_messages_tokens
from .web import TOOLS_WEB
from .file import TOOLS_FILE
from .exec import TOOLS_EXEC
from .code import TOOLS_CODE
from .think import TOOLS_THINK
from .memory import TOOLS_MEMORY
from .plugin_mgr import get_plugin_tools, reload_plugins, get_plugin_spec

# 每个内置模块的 execute 函数，按优先级排列（插件优先于内置）
_EXECUTORS = []

# Tool definition generators
_TOOL_SOURCES = []


def _register_builtin(tools: list[dict], executor):
    _TOOL_SOURCES.append(tools)
    _EXECUTORS.append(executor)


def _register_plugin_source():
    pass  # 插件通过 get_plugin_tools() 动态获取


# ── 注册所有内置模块 ──

from .web import execute as _exec_web
from .file import execute as _exec_file
from .exec import execute as _exec_exec
from .code import execute as _exec_code
from .think import execute as _exec_think
from .discover import execute as _exec_discover
from .plugin_mgr import execute as _exec_plugin_mgr
from .memory import execute as _exec_memory

_register_builtin(TOOLS_WEB, _exec_web)
_register_builtin(TOOLS_FILE, _exec_file)
_register_builtin(TOOLS_EXEC, _exec_exec)
_register_builtin(TOOLS_CODE, _exec_code)
_register_builtin(TOOLS_THINK, _exec_think)
_register_builtin(TOOLS_MEMORY, _exec_memory)
_register_builtin([
    {
        "type": "function",
        "function": {
            "name": "discover",
            "description": (
                "扫描所有可用能力（内置工具、已安装插件、可用 Skill），返回全景摘要。"
                "启动时或接到新任务时先调用此工具，了解自己有哪些能力可用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_spec",
            "description": "获取 Plugin 接口规范。写插件前先调用，了解必需接口和可选接口。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_plugin",
            "description": (
                "安装新插件——写入 Python 代码到 plugins/ 目录并热加载。"
                "当你从网上学会一种新能力后，用这个工具为自己安装。"
                "代码中必须定义 TOOLS + execute(name, args) + DESCRIPTION。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "插件名称"},
                    "code": {"type": "string", "description": "完整的 Python 插件代码"},
                },
                "required": ["name", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_plugins",
            "description": "列出已安装的插件及其工具和描述。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
], _exec_plugin_mgr)


def get_tools() -> list[dict]:
    """返回完整工具列表（内置 + 已加载插件），每次调用重建。"""
    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)
    tools.extend(get_plugin_tools())
    return tools


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行工具调用。遍历插件和内置模块，找到匹配的 execute。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    # 插件优先
    from .plugin_mgr import execute_plugin
    result = execute_plugin(name, args)
    if result is not None:
        return result

    # 内置模块
    for executor in _EXECUTORS:
        result = executor(name, args)
        if result is not None:
            return result

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


# 启动时加载插件
reload_plugins()
