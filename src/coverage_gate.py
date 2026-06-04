"""函数级关联测试门禁 — 精确到函数的修改权限控制。

核心理念：
  不是"全局覆盖率达到 N%"才能改，而是"你要改的那个函数，有测试保护吗？"

Tier 定义：
  tier_0_open:     src/tools/*, plugins/  →  0% (始终开放)
  tier_1_config:   配置/会话/命令模块      → 45%
  tier_2_core:     核心模块               → 60%
  tier_3_critical: 关键模块(guard, main)   → 75%

门禁逻辑：
  1. touched_functions 提供 → 函数级关联测试检查
     逐个检查要修改的函数是否有测试覆盖
  2. touched_functions 未提供 → 回退到全局覆盖率 tier 检查
  3. tier_0 始终放行，不走额外检查
"""

import ast
import json
import logging
import re
import subprocess
import time
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 分层定义 ──

class AccessLevel(Enum):
    READ_ONLY = "read_only"
    WRITE_WITH_BACKUP = "write"
    RESTRICTED = "restricted"


FILE_TIERS: dict[str, dict] = {
    "tier_0_open": {
        "patterns": [
            "src/tools/*.py",
            "src/tools/**/*.py",
            "plugins/*.py",
            "plugins/**/*.py",
        ],
        "threshold": 0.0,
        "description": "工具和插件 — 始终可修改",
    },
    "tier_1_config": {
        "patterns": [
            "src/config.py",
            "src/session.py",
            "src/suggest.py",
            "src/commands.py",
        ],
        "threshold": 0.45,
        "description": "配置和会话模块 — 需要 45% 覆盖率",
    },
    "tier_2_core": {
        "patterns": [
            "src/llm.py",
            "src/safety.py",
            "src/cache_context.py",
            "src/validate.py",
            "src/evolve.py",
            "src/coverage_gate.py",
            "src/evolution_loop.py",
        ],
        "threshold": 0.60,
        "description": "核心模块 — 需要 60% 覆盖率",
    },
    "tier_3_critical": {
        "patterns": [
            "src/guard.py",
            "src/main.py",
            "src/tools/__init__.py",
        ],
        "threshold": 0.75,
        "description": "关键模块 — 需要 75% 覆盖率",
    },
}

# ── 全局缓存 ──
# (total_pct, {file: pct}, {file: set_of_executed_lines}, timestamp)
_coverage_cache: tuple[float, dict[str, float], dict[str, set[int]], float] | None = None


# ═══════════════════════════════════════════════════════════════
# 模式匹配
# ═══════════════════════════════════════════════════════════════

def _match_pattern(filepath: str, pattern: str) -> bool:
    """glob 风格模式匹配。** 匹配任意深度，* 匹配单层任意字符。"""
    fp = str(Path(filepath)).replace("\\", "/")
    regex_parts: list[str] = []
    i = 0
    pat = pattern.replace("\\", "/")
    while i < len(pat):
        if pat[i:i+2] == "**":
            regex_parts.append(r".*")
            i += 2
        elif pat[i] == "*":
            regex_parts.append(r"[^/]*")
            i += 1
        else:
            j = i
            while j < len(pat) and pat[j] not in ("*",):
                j += 1
            regex_parts.append(re.escape(pat[i:j]))
            i = j
    regex = "^" + "".join(regex_parts) + "$"
    return bool(re.match(regex, fp))


def _get_tier(filepath: str) -> tuple[str, dict] | None:
    """确定文件属于哪个 tier。返回 (tier_name, tier_info) 或 None。"""
    fp = str(Path(filepath).resolve())
    try:
        rel = Path(fp).relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    rel_str = str(rel).replace("\\", "/")

    for tier_name, tier_info in FILE_TIERS.items():
        for pattern in tier_info["patterns"]:
            if _match_pattern(rel_str, pattern):
                return tier_name, tier_info
    return None


# ═══════════════════════════════════════════════════════════════
# 覆盖率测量（扩展：返回行级数据）
# ═══════════════════════════════════════════════════════════════

def _run_coverage() -> tuple[float, dict[str, float], dict[str, set[int]]]:
    """运行 pytest --cov 获取覆盖率数据。

    Returns:
        (total_pct, {filepath: pct}, {filepath: set_of_executed_lines})
    """
    # 快速失败：检查 pytest-cov 是否可用
    try:
        import coverage  # noqa: F401
    except ImportError:
        logger.warning(
            "覆盖率测量失败: pytest-cov 未安装。"
            "请执行 `pip install pytest-cov`。"
        )
        return 0.0, {}, {}

    try:
        result = subprocess.run(
            ["py", "-m", "pytest", "--cov=src", "--cov-report=json",
             "--cov-report=term", "-p", "no:cacheprovider", "-q", "--tb=no"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=120,
            encoding="utf-8", errors="replace",
        )
        if result.returncode not in (0, 1):
            logger.warning(
                f"覆盖率测量子进程异常退出 (exit {result.returncode})。"
                f"stderr: {result.stderr[:200]}"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.warning(f"覆盖率测量异常: {e}")
        return 0.0, {}, {}

    # 解析 coverage.json
    cov_json_path = PROJECT_ROOT / "coverage.json"
    if not cov_json_path.exists():
        return 0.0, {}, {}

    try:
        data = json.loads(cov_json_path.read_text(encoding="utf-8"))
        total_pct = data.get("totals", {}).get("percent_covered", 0.0) / 100.0
        file_cov: dict[str, float] = {}
        file_lines: dict[str, set[int]] = {}

        for fpath, finfo in data.get("files", {}).items():
            pct = finfo.get("summary", {}).get("percent_covered", 0.0) / 100.0
            file_cov[fpath] = pct
            file_lines[fpath] = set(finfo.get("executed_lines", []))

        return total_pct, file_cov, file_lines
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"解析 coverage.json 失败: {e}")

    return 0.0, {}, {}


def get_overall_coverage() -> float:
    """获取总体覆盖率（0.0-1.0），带 60 秒缓存。"""
    global _coverage_cache
    now = time.time()
    if _coverage_cache is not None and len(_coverage_cache) >= 4 and (now - _coverage_cache[3]) < 60:
        return _coverage_cache[0]
    total, files, lines = _run_coverage()
    _coverage_cache = (total, files, lines, now)
    return total
    total, files, lines = _run_coverage()
    _coverage_cache = (total, files, lines, now)
    return total


def get_file_coverage(filepath: str) -> float | None:
    """获取单个文件的覆盖率（0.0-1.0），无数据返回 None。"""
    global _coverage_cache
    if _coverage_cache is None or len(_coverage_cache) < 4 or (time.time() - _coverage_cache[3]) >= 60:
        get_overall_coverage()  # refresh
    if _coverage_cache is None:
        return None

    _, files, _, _ = _coverage_cache
    fp = str(Path(filepath).resolve())
    try:
        rel = str(Path(fp).relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return None
    return files.get(rel)


def _get_covered_lines(file_rel: str) -> set[int]:
    """从缓存的覆盖率数据中获取文件的已执行行号集合。"""
    global _coverage_cache
    if _coverage_cache is None or len(_coverage_cache) < 4 or (time.time() - _coverage_cache[3]) >= 60:
        get_overall_coverage()  # refresh
    if _coverage_cache is None:
        return set()

    _, _, line_data, _ = _coverage_cache
    return line_data.get(file_rel, set())


def _get_function_line_ranges(source: str) -> dict[str, tuple[int, int]]:
    """解析 Python 源码，返回 {函数名/类名: (起始行, 结束行)}。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    ranges: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            ranges[node.name] = (node.lineno, end)
        elif isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            ranges[node.name] = (node.lineno, end)

    return ranges


def _check_function_coverage(
    filepath: str, functions: list[str], file_rel: str
) -> tuple[bool, str]:
    """核心新逻辑：检查要修改的函数是否有测试覆盖。

    Args:
        filepath: 文件的绝对路径
        functions: 要修改的函数/方法名列表
        file_rel: 文件相对于 PROJECT_ROOT 的路径（用于匹配 coverage.json）

    Returns:
        (allowed, reason)
    """
    # 1. 解析源文件，获取每个函数的行范围
    try:
        source = Path(filepath).read_text(encoding="utf-8")
    except OSError as e:
        return False, f"无法读取源文件 {filepath}: {e}"

    func_ranges = _get_function_line_ranges(source)
    if not func_ranges:
        return True, (
            f"ok: {filepath} 中未解析到可测试的函数/类，"
            f"不做函数级检查（回退到全局覆盖率）"
        )

    # 2. 获取该文件已被测试执行的行
    covered_lines = _get_covered_lines(file_rel)

    # 3. 逐个检查每个函数
    uncovered: list[str] = []
    covered: list[str] = []
    not_found: list[str] = []

    for func_name in functions:
        if func_name in func_ranges:
            start, end = func_ranges[func_name]
            func_lines = set(range(start, end + 1))
            if func_lines & covered_lines:
                covered.append(func_name)
            else:
                uncovered.append(func_name)
        else:
            not_found.append(func_name)

    # 4. 构建结果
    parts: list[str] = []

    if not_found:
        parts.append(
            f"未在源文件中找到: {', '.join(not_found)}。"
            f"请确认函数名拼写正确。可用函数/类: "
            f"{', '.join(sorted(func_ranges.keys())[:20])}"
            f"{'...' if len(func_ranges) > 20 else ''}"
        )
        return False, " ".join(parts)

    if uncovered:
        parts.append(
            f"以下函数没有被任何测试覆盖: {', '.join(uncovered)}。"
            f"请先为它们创建测试用例，确保测试执行到这些函数。"
        )
        if covered:
            parts.append(f"已有覆盖的函数: {', '.join(covered)}。")
        return False, " ".join(parts)

    return True, (
        f"ok: 函数级关联测试检查通过。"
        f"所有声明的函数 ({', '.join(covered)}) 都有测试覆盖。"
    )


# ═══════════════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════════════

def can_modify(
    filepath: str,
    touched_functions: list[str] | None = None,
) -> tuple[bool, str]:
    """检查 agent 是否有权限修改指定文件。

    判断流程：
    1. tier_0 → 始终放行
    2. 其他 tier:
       - 有 touched_functions → 函数级关联测试检查
       - 无 → 全局覆盖率 tier 检查
       - 提供了 touched_functions → 函数级关联测试检查
       - 未提供 → 回退到全局覆盖率 tier 检查

    Args:
        filepath: 文件路径
        touched_functions: 计划修改的函数名列表（可选）

    Returns:
        (allowed, reason)
    """
    tier = _get_tier(filepath)
    if tier is None:
        return False, f"文件不在项目范围内: {filepath}"

    tier_name, tier_info = tier
    threshold = tier_info["threshold"]

    # tier_0: 始终开放
    if threshold <= 0.0:
        return True, f"ok: {filepath} 属于 {tier_name}（{tier_info['description']}），始终可修改"

    # 无 tier_4，所有 tier 均可通过函数级检查或全局覆盖率解锁
    # ── 函数级关联测试检查（优先）──
    if touched_functions:
        # 确保覆盖率数据是新鲜的
        get_overall_coverage()
        # 计算相对路径（用于匹配 coverage.json）
        fp = str(Path(filepath).resolve())
        try:
            file_rel = str(Path(fp).relative_to(PROJECT_ROOT)).replace("\\", "/")
        except ValueError:
            return False, f"无法计算文件相对路径: {filepath}"

        allowed, reason = _check_function_coverage(fp, touched_functions, file_rel)
        if allowed:
            return True, reason
        # 函数级检查不通过，给出精确指导
        return False, (
            f"⛔ 函数级关联测试不通过 ({tier_name}): {reason}"
        )

    # ── 回退：全局覆盖率 tier 检查 ──
    coverage = get_overall_coverage()

    if coverage >= threshold:
        return True, (
            f"ok: {filepath} ({tier_name}) — 全局覆盖率 {coverage:.0%} >= {threshold:.0%}，允许修改。"
            f"提示：提供 touched_functions 参数可获得更精确的函数级检查。"
        )
    else:
        gap = threshold - coverage
        return False, (
            f"{filepath} ({tier_name}) — 当前全局覆盖率 {coverage:.0%}，"
            f"需要 {threshold:.0%}（差 {gap:.0%}）。"
            f"可以：1) 添加测试提升全局覆盖率，或 2) 声明 touched_functions 走函数级检查。"
        )


def get_access_level(
    filepath: str,
    touched_functions: list[str] | None = None,
) -> AccessLevel:
    """确定文件的访问级别。支持函数级关联测试检查。"""
    tier = _get_tier(filepath)
    if tier is None:
        return AccessLevel.READ_ONLY

    _tier_name, tier_info = tier
    if tier_info["threshold"] <= 0.0:
        return AccessLevel.WRITE_WITH_BACKUP

    # 函数级检查或全局覆盖率
    if touched_functions:
        allowed, _ = can_modify(filepath, touched_functions=touched_functions)
    else:
        allowed, _ = can_modify(filepath)

    return AccessLevel.WRITE_WITH_BACKUP if allowed else AccessLevel.READ_ONLY


def get_modifiable_files() -> list[str]:
    """返回当前 agent 有写权限的所有 src/*.py 文件列表。"""
    coverage = get_overall_coverage()
    modifiable: list[str] = []

    for py_file in PROJECT_ROOT.glob("src/**/*.py"):
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        tier = _get_tier(str(py_file))
        if tier is None:
            continue
        _tier_name, tier_info = tier
        if tier_info["threshold"] <= 0.0 or coverage >= tier_info["threshold"]:
            modifiable.append(rel)

    return sorted(modifiable)


def get_tier_summary() -> str:
    """生成可读的各层解锁状态摘要。"""
    coverage = get_overall_coverage()
    lines = ["## 覆盖率门禁状态", f"当前总体覆盖率: {coverage:.0%}", ""]

    for tier_name in ["tier_0_open", "tier_1_config", "tier_2_core",
                       "tier_3_critical"]:
        tier_info = FILE_TIERS[tier_name]
        unlocked = coverage >= tier_info["threshold"]
        icon = "解锁" if unlocked else "锁定"
        desc = tier_info["description"]
        threshold_str = f"{tier_info['threshold']:.0%}"
        lines.append(f"  [{icon}] {tier_name}: {desc}（门槛: {threshold_str}）")

    return "\n".join(lines)


def get_coverage_gap(tier_name: str) -> tuple[float, str] | None:
    """返回达到某一层还需要多少覆盖率。返回 (gap, 建议)。"""
    if tier_name not in FILE_TIERS:
        return None
    tier_info = FILE_TIERS[tier_name]
    coverage = get_overall_coverage()
    if coverage >= tier_info["threshold"]:
        return 0.0, f"{tier_name} 已解锁"
    gap = tier_info["threshold"] - coverage
    return gap, (
        f"需要额外 {gap:.0%} 覆盖率才能解锁 {tier_name} ({tier_info['description']})。"
        f"建议为未测试的 src/ 模块添加单元测试。"
    )


def suggest_tests_to_unlock(
    target_file: str,
    touched_functions: list[str] | None = None,
) -> str:
    """建议需要添加哪些测试才能修改目标文件。

    如果提供了 touched_functions，给出函数级精确建议；
    否则给出覆盖率整体建议。
    """
    tier = _get_tier(target_file)
    if tier is None:
        return f"{target_file} 不在项目范围内，无法分析。"

    tier_name, tier_info = tier
    if tier_info["threshold"] <= 0.0:
        return f"{target_file} ({tier_name}) 始终可修改，无需额外测试。"

    # ── 函数级建议 ──
    if touched_functions:
        fp = str(Path(target_file).resolve())
        try:
            file_rel = str(Path(fp).relative_to(PROJECT_ROOT)).replace("\\", "/")
        except ValueError:
            return f"无法计算相对路径: {target_file}"

        try:
            source = Path(fp).read_text(encoding="utf-8")
        except OSError:
            return f"无法读取文件: {target_file}"

        func_ranges = _get_function_line_ranges(source)
        covered_lines = _get_covered_lines(file_rel)

        suggestions: list[str] = []
        for func_name in touched_functions:
            if func_name in func_ranges:
                start, end = func_ranges[func_name]
                if not (set(range(start, end + 1)) & covered_lines):
                    suggestions.append(
                        f"  - {func_name}() (第 {start}-{end} 行) — 未被测试覆盖"
                    )

        if suggestions:
            return (
                f"要修改 {target_file} 中的以下函数，需要先添加测试：\n"
                + "\n".join(suggestions)
                + f"\n\n建议在 tests/test_{Path(target_file).stem}.py 中为这些函数创建测试用例。"
            )
        return f"所有声明的函数都已有测试覆盖，可以修改 {target_file}。"

    # ── 覆盖率整体建议 ──
    coverage = get_overall_coverage()
    if coverage >= tier_info["threshold"]:
        return f"{target_file} ({tier_name}) 已解锁（覆盖率 {coverage:.0%}）。"

    gap = tier_info["threshold"] - coverage
    return (
        f"{target_file} ({tier_name}) 需要 {tier_info['threshold']:.0%} 覆盖率，"
        f"当前 {coverage:.0%}，差 {gap:.0%}。\n"
        f"建议为未测试的模块添加单元测试。"
        f"或者使用函数级检查：声明 touched_functions 指定要修改的函数名。"
    )


def get_unlocked_count() -> int:
    """返回已解锁的 tier 数量。"""
    coverage = get_overall_coverage()
    return sum(
        1 for info in FILE_TIERS.values() if coverage >= info["threshold"]
    )


def clear_cache() -> None:
    """清空覆盖率缓存，强制下次重新测量。"""
    global _coverage_cache
    _coverage_cache = None
