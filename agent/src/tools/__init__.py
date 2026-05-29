"""工具系统：注册、调度、热加载。"""

import importlib
import json
import sys

from openai.types.chat import ChatCompletionMessageToolCall

from .tokens import truncate_to_budget, estimate_tokens, count_messages_tokens
from .plugin_mgr import get_plugin_tools, reload_plugins, get_plugin_spec

# ── 内置模块清单（供热加载遍历）──
# (模块路径, TOOLS_变量名, execute函数名)
_BUILTIN_MODULES: list[tuple[str, str, str]] = [
    (".web",       "TOOLS_WEB",   "execute"),
    (".git",       "TOOLS_GIT",  "execute"),
    (".file",      "TOOLS_FILE",  "execute"),
    (".exec",      "TOOLS_EXEC",  "execute"),
    (".code",      "TOOLS_CODE",  "execute"),
    (".think",     "TOOLS_THINK", "execute"),
    (".memory",    "TOOLS_MEMORY","execute"),
]

# discover + plugin_mgr 的工具直接定义在 __init__.py 的 _register_builtin 里
_PLUGIN_MGR_TOOLS = [
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
]

# ── 注册表 ──
_TOOL_SOURCES: list[list[dict]] = []
_EXECUTORS: list = []


def _register_builtin(tools: list[dict], executor):
    _TOOL_SOURCES.append(tools)
    _EXECUTORS.append(executor)


# ── 首次加载：导入并注册所有内置模块 ──

def _init_registry():
    """初始化注册表（首次启动时调用，热加载时也调用）。"""
    _TOOL_SOURCES.clear()
    _EXECUTORS.clear()

    for mod_path, tools_attr, exec_attr in _BUILTIN_MODULES:
        full = f"src.tools{mod_path}"
        mod = importlib.import_module(full)
        tools_list = getattr(mod, tools_attr, [])
        exec_fn = getattr(mod, exec_attr, None)
        if tools_list or exec_fn:
            _register_builtin(tools_list, exec_fn)

    # plugin_mgr + discover 工具定义在此
    from .discover import execute as _exec_discover
    from .plugin_mgr import execute as _exec_plugin_mgr
    _register_builtin(_PLUGIN_MGR_TOOLS, _exec_plugin_mgr)


_init_registry()


# ── 工具列表构建 ──


def get_tools() -> list[dict]:
    """返回完整工具列表（内置 + 已加载插件），每次调用重建。"""
    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)
    tools.extend(get_plugin_tools())
    return tools


# ── 工具执行 ──


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


# ── 热加载 ──


def reload_tools() -> list[str]:
    """热加载所有内置工具模块。
    修改 tools/web.py / file.py / exec.py 等后调用此函数，无需重启 Agent。
    返回加载的模块名列表。
    """
    loaded: list[str] = []

    # 1. 清除模块缓存，强制重新导入
    for mod_path, _, _ in _BUILTIN_MODULES:
        full = f"src.tools{mod_path}"
        if full in sys.modules:
            del sys.modules[full]
            loaded.append(full)

    # 2. 重建 discover + plugin_mgr 缓存
    for mod_name in ["src.tools.discover", "src.tools.plugin_mgr"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    # 3. 重新初始化注册表
    _init_registry()

    # 4. 刷新插件
    from .plugin_mgr import _plugins as _plugin_cache
    _plugin_cache.clear()
    reload_plugins()

    return loaded


# 启动时加载插件
reload_plugins()
