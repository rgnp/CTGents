"""派生工具元数据 — 唯一真相源。

所有 frozenset 和 label 映射从此派生。其他文件不应硬编码工具名。
_add_meta 到 TOOLS_* 列表 → 此模块自动收编 → 消费者导入即可。

设计约束：此模块不 import __init__.py，避免循环导入。
"""

import importlib
import logging
import sys

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 模块注册表 — 所有工具模块的唯一登记处
# ═══════════════════════════════════════════════════════════════

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
    (".evolve",    "TOOLS_EVOLVE", "execute"),
    (".self",      "TOOLS_SELF",   "execute"),
    (".analyzer_tool", "TOOLS_ANALYZER", "execute"),
]

# ── 别名：不在任何 TOOLS_* 列表中但被消费者引用的工具名 ──
_META_ALIASES: dict[str, dict] = {
    "read_file_lines": {
        "label": "读取文件",
        "parallel_safe": True,
        "skip_compress": True,
    },
}

# ═══════════════════════════════════════════════════════════════
# 派生元数据（模块导入时自动填充）
# ═══════════════════════════════════════════════════════════════

TOOL_LABELS: dict[str, str] = {}
PARALLEL_SAFE: frozenset[str] = frozenset()
PLAN_BLOCKED: frozenset[str] = frozenset()
SKIP_COMPRESS_TOOLS: frozenset[str] = frozenset()
DEDUP_BLACKLIST: frozenset[str] = frozenset()


def _load_raw_tools() -> list[dict]:
    """导入所有工具模块，返回含 _meta 的原始工具定义列表。"""
    tools: list[dict] = []
    for mod_path, tools_attr, _exec_attr in _BUILTIN_MODULES:
        full = f"src.tools{mod_path}"
        try:
            mod = importlib.import_module(full)
            tools_list = getattr(mod, tools_attr, [])
            tools.extend(tools_list)
        except Exception:
            logger.warning("Failed to import tool module: %s", full)
    return tools


def _derive() -> tuple[dict[str, str], frozenset[str], frozenset[str],
                       frozenset[str], frozenset[str]]:
    """从工具定义的 _meta 字段派生所有元数据集合。"""
    tools = _load_raw_tools()

    labels: dict[str, str] = {}
    parallel_safe: set[str] = set()
    plan_blocked: set[str] = set()
    skip_compress: set[str] = set()
    dedup_blacklist: set[str] = set()

    for t in tools:
        meta = t.get("_meta", {})
        name = t["function"]["name"]

        labels[name] = meta.get("label", name)
        if meta.get("parallel_safe"):
            parallel_safe.add(name)
        if meta.get("plan_blocked"):
            plan_blocked.add(name)
        if meta.get("skip_compress"):
            skip_compress.add(name)
        if meta.get("dedup_blacklist"):
            dedup_blacklist.add(name)

    # 合并别名
    for alias_name, alias_meta in _META_ALIASES.items():
        labels[alias_name] = alias_meta.get("label", alias_name)
        if alias_meta.get("parallel_safe"):
            parallel_safe.add(alias_name)
        if alias_meta.get("plan_blocked"):
            plan_blocked.add(alias_name)
        if alias_meta.get("skip_compress"):
            skip_compress.add(alias_name)
        if alias_meta.get("dedup_blacklist"):
            dedup_blacklist.add(alias_name)

    return (
        labels,
        frozenset(parallel_safe),
        frozenset(plan_blocked),
        frozenset(skip_compress),
        frozenset(dedup_blacklist),
    )


def _refresh_globals() -> None:
    """重新派生并更新模块级全局变量。供热加载调用。"""
    global TOOL_LABELS, PARALLEL_SAFE, PLAN_BLOCKED, SKIP_COMPRESS_TOOLS, DEDUP_BLACKLIST
    (TOOL_LABELS, PARALLEL_SAFE, PLAN_BLOCKED,
     SKIP_COMPRESS_TOOLS, DEDUP_BLACKLIST) = _derive()


# 首次导入时计算
_refresh_globals()
