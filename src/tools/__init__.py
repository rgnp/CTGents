"""工具系统：注册、调度、热加载。"""

import importlib
import json
import logging
import sys
import time
from openai.types.chat import ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)

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
    (".rag",       "TOOLS_RAG",    "execute"),
    (".evolve",    "TOOLS_EVOLVE",  "execute"),
    (".self",      "TOOLS_SELF",   "execute"),
]

_TOOL_SOURCES: list[list[dict]] = []
_EXECUTORS: list = []

def _register_builtin(tools: list[dict], executor):
    _TOOL_SOURCES.append(tools)
    _EXECUTORS.append(executor)

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

_init_registry()


# ── 工具列表缓存 ──
_tools_cache: list[dict] | None = None

# ── Plan Mode ──
_plan_mode: bool = False

# Plan mode 下禁用的写工具
_PLAN_BLOCKED: frozenset[str] = frozenset({
    "write_file", "edit_file_lines", "delete_file",
    "git_commit", "git_push", "git_pr",
    "remember", "forget",  # 记忆写入也禁掉，保持 plan 纯粹
})


def set_plan_mode(enabled: bool) -> None:
    """切换 Plan Mode。只读模式：禁用写工具，仅保留读/分析工具。

    不修改消息结构、不改变 prefix —— 纯粹的工具列表过滤。
    """
    global _plan_mode, _tools_cache
    _plan_mode = enabled
    _tools_cache = None  # 强制重建工具列表


def is_plan_mode() -> bool:
    return _plan_mode


def get_tools() -> list[dict]:
    """返回工具列表。plan mode 下过滤写工具。

    结果缓存复用，确保每轮返回的 tools 是同一个 list 对象，
    OpenAI SDK 序列化后字节一致，保障 DeepSeek 前缀缓存命中。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)

    if _plan_mode:
        tools = [t for t in tools
                 if t.get("function", {}).get("name") not in _PLAN_BLOCKED]

    _tools_cache = tools
    return tools
# ── 工具执行 ──


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行工具调用。遍历内置模块找到匹配的 execute。"""
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

    # 内置模块
    for executor in _EXECUTORS:
        result = executor(name, args)
        if result is not None:
            break

    if result is not None:
        storm_record(name, args, result)
        elapsed = time.perf_counter() - t0
        logger.debug("工具调用: %s (%.2f秒)", name, elapsed)
        return result

    error_msg = f"未注册的工具: {name}"
    logger.error(error_msg)
    return error_msg

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

    # 2. 重新初始化注册表
    _init_registry()

    return loaded
