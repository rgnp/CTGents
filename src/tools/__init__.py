"""工具系统：注册、调度、热加载。"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from collections import OrderedDict

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

# ── 可选工具组（load-on-demand）──
# 领域专用工具（如文献研究）默认不挂常驻 prefix——省 token、不做该领域时不背着它们。
# 组名标在工具 _meta["group"] 上；这里列哪些组默认关。做该领域时用 /tools load <组> 挂上，
# 或开会话前设 CTG_TOOL_GROUPS=research。改前缀缓存不是约束（已判服务端问题）。
_OPTIONAL_GROUPS: frozenset[str] = frozenset({"research", "rag", "repo", "git", "memory-mutate", "files-mutate"})
_enabled_groups: set[str] = set()


def _load_default_groups() -> None:
    raw = os.getenv("CTG_TOOL_GROUPS", "")
    for g in raw.replace(",", " ").split():
        if g in _OPTIONAL_GROUPS:
            _enabled_groups.add(g)


_load_default_groups()


def enable_tool_group(name: str) -> str:
    """挂上一个可选工具组（失效缓存，下次 get_tools 带上它）。"""
    global _tools_cache
    if name not in _OPTIONAL_GROUPS:
        return f"未知工具组 '{name}'。可选：{', '.join(sorted(_OPTIONAL_GROUPS)) or '（无）'}"
    if name in _enabled_groups:
        return f"工具组 '{name}' 已加载。"
    _enabled_groups.add(name)
    _tools_cache = None
    return f"✅ 已加载工具组 '{name}'。"


def disable_tool_group(name: str) -> str:
    """卸下一个可选工具组。"""
    global _tools_cache
    if name in _enabled_groups:
        _enabled_groups.discard(name)
        _tools_cache = None
        return f"✅ 已卸载工具组 '{name}'。"
    return f"工具组 '{name}' 未加载。"


def list_tool_groups() -> str:
    lines = ["可选工具组（load-on-demand，默认不占常驻 prefix）："]
    for g in sorted(_OPTIONAL_GROUPS):
        lines.append(f"  {g}: {'已加载' if g in _enabled_groups else '未加载'}")
    return "\n".join(lines)


def get_tools() -> list[dict]:
    """返回当前生效的工具列表（剥离 _meta；按可选组过滤）。

    剥离 _meta 后缓存——API 不可见元数据。结果缓存复用，确保每轮返回同一 list 对象、
    字节一致以保 DeepSeek 前缀缓存命中（enable/disable 组会失效缓存重建）。
    """
    global _tools_cache
    if _tools_cache is not None:
        return _tools_cache

    tools: list[dict] = []
    for src in _TOOL_SOURCES:
        for t in src:
            grp = t.get("_meta", {}).get("group")
            if grp in _OPTIONAL_GROUPS and grp not in _enabled_groups:
                continue  # 可选组未加载 → 不挂进 prefix
            tools.append(t)

    # 剥离 _meta（API 不可见）
    tools = [{k: v for k, v in t.items() if k != "_meta"} for t in tools]

    _tools_cache = tools
    return tools


_tools_subset_cache: dict[frozenset[str], list[dict]] = {}


def get_tools_subset(names: frozenset[str]) -> list[dict]:
    """按名取工具 schema 子集（剥 _meta）——供 delegate worker 等隔离调用方拼自己的工具单。

    刻意绕过 _enabled_groups 过滤：组机制只决定"挂不挂进主会话 prefix"（省 token），
    execute_tool 派发不看组——worker 拿到 schema 就能用（如默认关闭的 research 组里的
    rag_search）。结果按 names 缓存，保证同一子集每次返回同一 list 对象、字节稳定。
    """
    cached = _tools_subset_cache.get(names)
    if cached is not None:
        return cached
    subset = [
        {k: v for k, v in t.items() if k != "_meta"}
        for src in _TOOL_SOURCES
        for t in src
        if t["function"]["name"] in names
    ]
    _tools_subset_cache[names] = subset
    return subset
# ── 工具执行 ──
# ── 会话级工具调用缓存 ──
#
# 与 Storm（轮内去重）互补：Storm 处理同轮重复调用，写操作即全清；
# 会话缓存跨轮次保持读结果，TTL 兜底，写操作不清。
# 不重复覆盖 file.py 已自带的 read_file / list_files 缓存。
_SESSION_CACHE_TTL = 300        # 5 分钟
_SESSION_CACHE_MAX = 200        # 最大条目数
_session_cache: OrderedDict = OrderedDict()
_session_cache_hit: int = 0
_session_cache_miss: int = 0
# 白名单：只有纯读、确定性的工具才进缓存
_SESSION_CACHEABLE: frozenset[str] = frozenset({
    "grep_code", "find_files", "count_lines", "read_page",
    "scan_papers", "read_papers",
})


def _session_cache_key(name: str, args: dict) -> tuple:
    """生成缓存键：归一化参数顺序 + 排除 None 值抖动。"""
    clean = {k: v for k, v in args.items() if v is not None}
    return (name, json.dumps(clean, sort_keys=True, ensure_ascii=False))


def _session_cache_get(name: str, args: dict) -> str | None:
    """查缓存。命中且未过期 → 返回结果；过期/不在 → 返回 None。"""
    global _session_cache_hit, _session_cache_miss
    if name not in _SESSION_CACHEABLE:
        _session_cache_miss += 1
        return None
    key = _session_cache_key(name, args)
    entry = _session_cache.get(key)
    if entry is None:
        _session_cache_miss += 1
        return None
    ts, result = entry
    if time.time() - ts > _SESSION_CACHE_TTL:
        del _session_cache[key]
        _session_cache_miss += 1
        return None
    _session_cache_hit += 1
    return (
        f"[⚡会话缓存] [{name}] 以下为缓存结果（{_SESSION_CACHE_TTL}秒内未过期）：\n{result}"
    )


def _session_cache_set(name: str, args: dict, result: str) -> None:
    """存缓存。超过容量时驱逐最旧条目（LRU）。"""
    if name not in _SESSION_CACHEABLE:
        return
    key = _session_cache_key(name, args)
    _session_cache[key] = (time.time(), result)
    _session_cache.move_to_end(key)
    while len(_session_cache) > _SESSION_CACHE_MAX:
        _session_cache.popitem(last=False)


def get_session_cache_stats() -> dict:
    """返回会话缓存命中统计（供 self 工具展示）。"""
    return {
        "hit": _session_cache_hit,
        "miss": _session_cache_miss,
        "size": len(_session_cache),
        "max": _SESSION_CACHE_MAX,
        "ttl": _SESSION_CACHE_TTL,
    }



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

    # ── 会话缓存检查 ──（纯读工具跨轮次复用）
    cached = _session_cache_get(name, args)
    if cached is not None:
        return cached

    result: str | None = None
    error_msg = ""

    # 内置模块
    for executor in _EXECUTORS:
        result = executor(name, args)
        if result is not None:
            break

    if result is not None:
        storm_record(name, args, result)
        _session_cache_set(name, args, result)
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
