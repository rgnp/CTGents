"""工具系统：注册、调度、热加载。"""

import importlib
import json
import logging
import sys
import time

from openai.types.chat import ChatCompletionMessageToolCall

logger = logging.getLogger(__name__)

_TOOL_SOURCES: list[list[dict]] = []
_EXECUTORS: list = []
# 故障隔离记录：上次初始化中导入失败、已被跳过的模块 (模块路径, 错误)。
_FAILED_MODULES: list[tuple[str, str]] = []


def _register_builtin(tools: list[dict], executor):
    _TOOL_SOURCES.append(tools)
    _EXECUTORS.append(executor)


def get_failed_modules() -> list[tuple[str, str]]:
    """返回上次注册表初始化中被隔离的工具模块。

    含两类：发现阶段语法错误（ast.parse 失败）+ 导入阶段运行时错误。
    """
    from ._tool_meta import get_discovery_skipped
    return list(_FAILED_MODULES) + get_discovery_skipped()


def _init_registry():
    """初始化注册表（首次启动时调用，热加载时也调用）。

    先重新发现工具模块（捡起新增/删除的文件），再逐个加载。故障隔离：单个工具模块
    导入失败（如自进化长出的工具有运行时错误）不拖垮整个注册表——坏模块被跳过并记入
    _FAILED_MODULES，其余工具照常注册，系统保持可用。
    """
    global _tools_cache
    _tools_cache = None
    _TOOL_SOURCES.clear()
    _EXECUTORS.clear()
    _FAILED_MODULES.clear()

    from . import _tool_meta
    _tool_meta.refresh_modules()
    for mod_path, tools_attr, exec_attr in _tool_meta._BUILTIN_MODULES:
        full = f"src.tools{mod_path}"
        try:
            if full in sys.modules:
                importlib.reload(sys.modules[full])
            mod = importlib.import_module(full)
            tools_list = getattr(mod, tools_attr, [])
            exec_fn = getattr(mod, exec_attr, None)
            if tools_list or exec_fn:
                _register_builtin(tools_list, exec_fn)
        except Exception as e:
            _FAILED_MODULES.append((full, f"{type(e).__name__}: {e}"))
            logger.error("工具模块加载失败，已隔离跳过: %s — %s", full, e)
            sys.modules.pop(full, None)

    _tool_meta._refresh_globals()


_init_registry()


# ── 工具列表缓存 ──
_tools_cache: list[dict] | None = None


def get_tools() -> list[dict]:
    """返回全部工具列表。

    剥离 _meta 后缓存——API 不可见元数据。
    结果缓存复用，确保每轮返回的 tools 是同一个 list 对象，
    OpenAI SDK 序列化后字节一致，保障 DeepSeek 前缀缓存命中。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        tools.extend(src)

    # 剥离 _meta（API 不可见）
    tools = [{k: v for k, v in t.items() if k != "_meta"} for t in tools]

    _tools_cache = tools
    return tools
# ── 工具执行 ──


def execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行工具调用。遍历内置模块找到匹配的 execute。"""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    t0 = time.perf_counter()

    # ── 工具边界拦截：强规则(C10 读后写 / C14 目录)在执行前机械校验 ──
    from .tool_guard import check as _guard_check
    rejection = _guard_check(name, args)
    if rejection is not None:
        logger.info("工具拦截: %s — %s", name, rejection)
        return rejection

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
            importlib.import_module("src.commands")
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
    from . import _tool_meta
    for mod_path, _, _ in _tool_meta._BUILTIN_MODULES:
        full = f"src.tools{mod_path}"
        if full in sys.modules:
            del sys.modules[full]
            loaded.append(full)

    # 2. 重新初始化注册表（会重新发现工具文件，捡起新增的）
    _init_registry()

    return loaded
