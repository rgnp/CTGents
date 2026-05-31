"""Storm — 工具调用滑动窗口去重。

同轮工具循环中，相同 tool + 相同参数连续调用时，跳过执行并标记重复。
只对纯读取工具生效（有副作用的工具不进窗口）。

线程安全：使用 threading.Lock 保护窗口，支持 SAFE 并行分发。

用法：
  from .storm import storm_check, reset_storm, get_storm_stats

  reset_storm()           # 新轮次开始时调用
  result = storm_check("read_file", {"path": "main.py"})
  if result:
      return result       # 跳过，返回重复标记
  # ... 正常执行 ...
"""

import json
import threading

# ── 黑名单：有副作用的工具，永远不去重 ──
_DEDUP_BLACKLIST: frozenset[str] = frozenset({
    # 文件修改
    "write_file", "edit_file_lines", "undo_edit", "delete_file",
    # 命令执行
    "run_command", "run_python",
    # Git 变更
    "git_commit", "git_push", "git_pr",
    # 记忆系统
    "remember", "forget",
    # 插件系统
    "install_plugin",
    # MCP 连接管理
    "mcp_connect", "mcp_disconnect",
    # RAG 索引变更
    "rag_index",
    # 策略思考（重复意味着场景已变，需要重新评估）
    "think",
    # MCP 配置持久化
    "mcp_save_config",
})

# 窗口大小
_WINDOW_SIZE = 8

# 滑动窗口 + 锁 + 统计
_window: list[int] = []
_lock = threading.Lock()
_dedup_hits: int = 0


def _hash_call(name: str, args: dict) -> int:
    """生成工具调用的哈希值。

    用 sort_keys 确保参数顺序不影响哈希。
    排除 None/空值参数的抖动：删掉 value 为 None 的键。
    """
    clean = {k: v for k, v in args.items() if v is not None}
    normalized = json.dumps(clean, sort_keys=True, ensure_ascii=False)
    return hash((name, normalized))


def reset_storm() -> None:
    """重置滑动窗口和拦截计数。每轮对话开始前调用一次。"""
    global _window, _dedup_hits
    with _lock:
        _window = []
        _dedup_hits = 0


def _is_blacklisted(name: str) -> bool:
    """检查工具是否在黑名单中。"""
    return name in _DEDUP_BLACKLIST


def storm_check(name: str, args: dict) -> str | None:
    """检查工具调用是否重复。线程安全。

    命中时在终端打印可视标记，同时更新拦截计数。

    Args:
        name: 工具名
        args: 参数字典

    Returns:
        str  — 重复标记（直接返回给 LLM，跳过执行）
        None — 不是重复，正常执行
    """
    global _dedup_hits
    if _is_blacklisted(name):
        return None

    clean = {k: v for k, v in args.items() if v is not None}
    h = _hash_call(name, args)

    with _lock:
        if h in _window:
            _dedup_hits += 1
            arg_str = json.dumps(clean, ensure_ascii=False)
            print(f"  🔁 [Storm] 去重拦截: {name}({arg_str})")
            return f"[⚡重复调用] [{name}] 相同参数已在上文返回，结果可用。如需刷新请用不同参数。"

        _window.append(h)
        if len(_window) > _WINDOW_SIZE:
            _window.pop(0)

    return None


def get_storm_stats() -> dict:
    """返回 Storm 去重统计。

    Returns:
        dict: {"hits": int, "window_size": int}
    """
    with _lock:
        return {"hits": _dedup_hits, "window_size": len(_window)}


def get_blacklist() -> frozenset[str]:
    """返回黑名单集合（用于调试/诊断）。"""
    return _DEDUP_BLACKLIST
