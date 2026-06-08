"""派生工具元数据 — 唯一真相源。

所有 frozenset 和 label 映射从此派生。其他文件不应硬编码工具名。
_add_meta 到 TOOLS_* 列表 → 此模块自动收编 → 消费者导入即可。

设计约束：此模块不 import __init__.py，避免循环导入。
"""

import ast
import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════════
# 模块注册表 — 自动发现 src/tools/*.py（含 TOOLS_* + execute() 的文件）
#
# 进化产物只需把新工具文件放进 src/tools/ 即自动注册，无需手改任何核心文件——
# 这消除了"为加工具而编辑此清单"导致的整类启动崩溃。
# ═══════════════════════════════════════════════════════════════

# 发现阶段因语法错误被跳过的工具文件 (文件名, 错误)，防御性可见。
_DISCOVERY_SKIPPED: list[tuple[str, str]] = []


def _find_tools_attr(tree: ast.Module) -> str | None:
    """找模块级 TOOLS_* 变量名（普通或带注解赋值）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("TOOLS_"):
                    return target.id
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.target.id.startswith("TOOLS_")):
            return node.target.id
    return None


def _discover_builtin_modules() -> list[tuple[str, str, str]]:
    """扫描 src/tools/*.py，返回 (模块路径, TOOLS_变量名, "execute") 列表。

    防御：每个文件先 ast.parse；语法错误的文件被跳过并记入 _DISCOVERY_SKIPPED，
    而不是让 import 阶段崩溃整个启动（这正是上次手改注册表导致崩溃的那类故障）。
    """
    _DISCOVERY_SKIPPED.clear()
    result: list[tuple[str, str, str]] = []
    for py_file in sorted(_TOOLS_DIR.glob("*.py")):
        if py_file.stem.startswith("_") or py_file.stem.startswith("test"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError) as e:
            _DISCOVERY_SKIPPED.append((py_file.name, f"{type(e).__name__}: {e}"))
            logger.error("工具文件语法错误，已跳过自动发现: %s — %s", py_file.name, e)
            continue
        tools_attr = _find_tools_attr(tree)
        has_execute = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "execute"
            for n in tree.body
        )
        if tools_attr and has_execute:
            result.append((f".{py_file.stem}", tools_attr, "execute"))
    return result


def get_discovery_skipped() -> list[tuple[str, str]]:
    """返回发现阶段因语法错误被跳过的工具文件。"""
    return list(_DISCOVERY_SKIPPED)


_BUILTIN_MODULES: list[tuple[str, str, str]] = _discover_builtin_modules()


def refresh_modules() -> None:
    """重新发现工具模块（热加载时调用，捡起新增/删除的工具文件）。"""
    global _BUILTIN_MODULES
    _BUILTIN_MODULES = _discover_builtin_modules()


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
