"""工具系统：注册、调度、热加载。"""

import importlib
import json
import sys
import time
from openai.types.chat import ChatCompletionMessageToolCall

from .plugin_mgr import get_plugin_tools, reload_plugins

# ── 内置模块清单（供热加载遍历）──
# (模块路径, TOOLS_变量名, execute函数名)
_BUILTIN_MODULES: list[tuple[str, str, str]] = [
    (".web",       "TOOLS_WEB",    "execute"),
    (".file",      "TOOLS_FILE",   "execute"),
    (".exec",      "TOOLS_EXEC",   "execute"),
    (".code",      "TOOLS_CODE",   "execute"),
    (".think",     "TOOLS_THINK",  "execute"),
    (".memory",    "TOOLS_MEMORY", "execute"),
    (".git",       "TOOLS_GIT",    "execute"),
    (".project",   "TOOLS_PROJECT","execute"),
    (".lint",      "TOOLS_LINT",   "execute"),
    (".mcp",       "TOOLS_MCP",    "execute"),
    (".rag",       "TOOLS_RAG",    "execute"),
    (".subagent",  "TOOLS_SUBAGENT", "execute"),
]
# discover + plugin_mgr 的工具直接定义在 __init__.py 的 _register_builtin 里
_PLUGIN_MGR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "discover",
            "description": "扫描所有可用能力（内置工具 + 插件），返回全景摘要。",
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
    global _tools_cache
    _tools_cache = None
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
    from .plugin_mgr import execute as _exec_plugin_mgr
    _register_builtin(_PLUGIN_MGR_TOOLS, _exec_plugin_mgr)


_init_registry()


# ── 工具列表构建 ──


# ── 工具列表缓存 ──
_tools_cache: list[dict] | None = None


def get_tools() -> list[dict]:
    """返回完整工具列表（内置 + 已加载插件）。

    结果缓存复用，确保每轮返回的 tools 是同一个 list 对象，
    OpenAI SDK 序列化后字节一致，保障 DeepSeek 前缀缓存命中。
    插件热加载后自动清空缓存。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)
    tools.extend(get_plugin_tools())
    # MCP 工具动态变化，不进缓存
    from .mcp import get_mcp_tools
    tools.extend(get_mcp_tools())
    _tools_cache = tools
    return tools



# ── 工具执行 ──


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行工具调用。遍历插件和内置模块，找到匹配的 execute。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    t0 = time.perf_counter()

    # ── Storm 去重检查 ──
    from .storm import storm_check, storm_record
    dup = storm_check(name, args)
    if dup is not None:
        return dup

    result: str | None = None
    error_msg = ""

    # 插件优先
    from .plugin_mgr import execute_plugin
    result = execute_plugin(name, args)

    # 内置模块
    if result is None:
        for executor in _EXECUTORS:
            result = executor(name, args)
            if result is not None:
                break

    # MCP 工具（server_name__tool_name 格式）
    if result is None:
        from .mcp import execute_mcp_tool
        result = execute_mcp_tool(name, args)

    if result is None:
        result = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        error_msg = "未知工具"

    # ── Storm 结果缓存 ──
    storm_record(name, args, result)

    # ── 追踪记录 ──
    from .tracker import record_call
    duration = (time.perf_counter() - t0) * 1000
    has_error = result.startswith('{"error":') if result else True
    record_call(name, args, success=not has_error, error=error_msg, duration_ms=duration)
    # ── 失败反思 ──
    if has_error:
        from .reflect import record_failure
        record_failure(name, args, error_msg or result[:200])

    # ── 自动热加载 ──
    if not has_error and name in ("write_file", "edit_file_lines"):
        filepath = args.get("path", "") or args.get("filepath", "")
        if isinstance(filepath, str) and ("src/" in filepath or "src\\" in filepath) and filepath.endswith(".py"):
            _auto_reload_module(filepath)

    return result

# ── 热加载 ──


def _filepath_to_module(filepath: str) -> str | None:
    """将文件路径映射为 Python 模块名。"""
    # Windows/Unix 通用：把路径标准化
    fp = filepath.replace("\\", "/")
    if fp.endswith(".py"):
        fp = fp[:-3]
    # 去掉项目根前缀
    for prefix in ("src/", "src\\"):
        if prefix.replace("\\", "/") in fp:
            idx = fp.index(prefix.replace("\\", "/"))
            fp = fp[idx + len(prefix.replace("\\", "/")):]
            break
    mod = "src." + fp.replace("/", ".")
    return mod


def _auto_reload_module(filepath: str) -> str | None:
    """自动热加载单个 Python 模块。修改 src/*.py 后自动调用。

    - 工具模块 → 重载 + 重建工具注册表
    - 命令模块 → 重建 dispatch
    - 其他模块 → 简单 importlib.reload
    """
    mod_name = _filepath_to_module(filepath)
    if not mod_name or mod_name not in sys.modules:
        return None  # 还未加载的模块无需 reload

    import importlib

    # ── 命令模块：需要重建 dispatch ──
    if mod_name == "src.commands":
        try:
            del sys.modules[mod_name]
            import src.commands  # type: ignore[no-redef]
            # 通知 main.py 刷新 dispatch_cmd（如果可访问）
            try:
                from src.main import _reload_dispatch
                _reload_dispatch()
            except Exception:
                pass
            return "🔄 已热加载: commands.py"
        except Exception as e:
            return f"⚠️ commands.py 热加载失败: {e}"

    # ── 工具模块：重载后重建注册表 ──
    is_tool = mod_name.startswith("src.tools.")
    try:
        importlib.reload(sys.modules[mod_name])
        if is_tool:
            _init_registry()
        return f"🔄 已热加载: {mod_name}"
    except Exception as e:
        return f"⚠️ {mod_name} 热加载失败: {e}"


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
