"""诊断层：把 tracker 的原始异常翻译成可行动的诊断。

tracker 只告诉你"X 慢了 5.3x"，不告诉你为什么、能不能修。
本模块读工具源码，识别慢/失败的根因模式，生成带建议和置信度的诊断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "src" / "tools"


@dataclass
class DiagnosticResult:
    """一条经过诊断的异常，含根因分析和建议。"""

    tool: str
    anomaly_type: str           # "slow" | "high_failure"
    anomaly_detail: str          # raw anomaly detail from tracker
    likely_cause: str            # human-readable root cause
    root_pattern: str            # machine-readable: "subprocess_overhead" | ...
    affected_files: list[str] = field(default_factory=list)
    suggested_action: str = ""   # concrete next step
    confidence: float = 0.0      # 0.0-1.0
    actionable: bool = False     # can this actually be improved?


def diagnose_anomalies(anomalies: list[dict]) -> list[DiagnosticResult]:
    """对一批 tracker 异常执行代码感知诊断。"""
    return [diagnose_one(a) for a in anomalies]


def diagnose_one(anomaly: dict) -> DiagnosticResult:
    """对单个异常深入诊断。"""
    tool = anomaly.get("tool", "")
    atype = anomaly.get("type", "")

    if atype == "slow":
        return _diagnose_slow(tool, anomaly)
    if atype == "high_failure":
        return _diagnose_failure(tool, anomaly)
    return DiagnosticResult(
        tool=tool,
        anomaly_type=atype,
        anomaly_detail=anomaly.get("detail", ""),
        likely_cause="未知异常类型",
        root_pattern="unknown",
    )


# ═══════════════════════════════════════════════════════════════
# 分类诊断
# ═══════════════════════════════════════════════════════════════


def _diagnose_slow(tool: str, anomaly: dict) -> DiagnosticResult:
    """诊断慢工具：函数级分析，避免文件级误判。"""
    detail = anomaly.get("detail", "")
    source_file = _find_tool_source(tool)
    # 函数级分析：只扫描该工具函数体，不扫全文件
    func_patterns = _analyze_function(source_file, tool)
    # 回退：函数体无结果时扫描文件级（网络/缓存是合理的文件级模式）
    file_patterns = _analyze_source(source_file)
    rel_path = str(source_file.relative_to(PROJECT_ROOT)) if source_file else ""

    # 函数体中有 subprocess → 确实是该工具用了外部进程
    if "subprocess" in func_patterns:
        actionable = "has_caching" not in func_patterns and "has_caching" not in file_patterns
        return DiagnosticResult(
            tool=tool,
            anomaly_type="slow",
            anomaly_detail=detail,
            likely_cause=(
                f"外部进程开销（{tool} 内部调用 subprocess，"
                "耗时主要来自被调用的命令本身，非 Python 开销）"
            ),
            root_pattern="subprocess_overhead",
            affected_files=[rel_path] if rel_path else [],
            suggested_action=_suggest_subprocess(tool),
            confidence=0.85,
            actionable=actionable,
        )

    # 网络 I/O（文件级检测 — requests/httpx 等是整个模块的模式）
    if "network" in file_patterns:
        return DiagnosticResult(
            tool=tool,
            anomaly_type="slow",
            anomaly_detail=detail,
            likely_cause="网络 I/O 耗时（HTTP 请求，延迟不可控）",
            root_pattern="network_io",
            affected_files=[rel_path] if rel_path else [],
            suggested_action="考虑：缓存可复用的结果 / 合并多次请求 / 增加本地 fallback。",
            confidence=0.80,
            actionable=True,
        )

    # 函数体中大量文件 I/O
    if "file_io" in func_patterns:
        return DiagnosticResult(
            tool=tool,
            anomaly_type="slow",
            anomaly_detail=detail,
            likely_cause="文件 I/O 耗时（读写较大文件或频繁操作）",
            root_pattern="file_io",
            affected_files=[rel_path] if rel_path else [],
            suggested_action="考虑：内存缓存（mtime 检测）/ 增量读取 / 延迟加载。",
            confidence=0.70,
            actionable=True,
        )

    return DiagnosticResult(
        tool=tool,
        anomaly_type="slow",
        anomaly_detail=detail,
        likely_cause="未识别明确的性能瓶颈模式，建议手动 profile。",
        root_pattern="unknown",
        affected_files=[rel_path] if rel_path else [],
        suggested_action="建议：用 cProfile / timeit 定位热点，或检查是否数据集增大。",
        confidence=0.30,
        actionable=True,
    )


def _diagnose_failure(tool: str, anomaly: dict) -> DiagnosticResult:
    """诊断高失败率工具。"""
    detail = anomaly.get("detail", "")
    source_file = _find_tool_source(tool)
    rel_path = str(source_file.relative_to(PROJECT_ROOT)) if source_file else ""

    return DiagnosticResult(
        tool=tool,
        anomaly_type="high_failure",
        anomaly_detail=detail,
        likely_cause="tracker 仅记录异常类型名，无完整 traceback，精确诊断受限。",
        root_pattern="insufficient_data",
        affected_files=[rel_path] if rel_path else [],
        suggested_action=(
            "建议：增强 tracker 记录最近 N 次异常的完整 traceback，"
            "下次同类异常即可自动定位根因。"
        ),
        confidence=0.40,
        actionable=True,
    )


# ═══════════════════════════════════════════════════════════════
# 源码分析
# ═══════════════════════════════════════════════════════════════

_TOOL_SOURCE_CACHE: dict[str, Path | None] = {}


def _find_tool_source(tool: str) -> Path | None:
    """查找工具的源文件（优先缓存，否则递归搜索 TOOLS_DIR）。"""
    if tool in _TOOL_SOURCE_CACHE:
        return _TOOL_SOURCE_CACHE[tool]

    if not TOOLS_DIR.exists():
        _TOOL_SOURCE_CACHE[tool] = None
        return None

    for f in TOOLS_DIR.rglob("*.py"):
        try:
            content = f.read_text(encoding="utf-8")
            if f"def {tool}(" in content:
                _TOOL_SOURCE_CACHE[tool] = f
                return f
        except OSError:
            continue

    _TOOL_SOURCE_CACHE[tool] = None
    return None


def _extract_function_body(source: str, func_name: str) -> str | None:
    """从 Python 源码中提取指定函数的函数体（缩进感知）。

    从 `def func_name(` 开始，找到函数结束（下一个同缩进级的非空行）。
    返回函数体源码文本；找不到返回 None。
    """
    lines = source.split("\n")
    start = -1
    for i, line in enumerate(lines):
        if re.match(rf"^\s*def\s+{re.escape(func_name)}\s*\(", line):
            start = i
            break
    if start == -1:
        return None

    # 获取函数定义的缩进
    def_indent = len(lines[start]) - len(lines[start].lstrip())

    # 收集函数体（下一行开始，缩进 > def_indent）
    body_lines: list[str] = []
    i = start + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == "":
            body_lines.append(line)
            i += 1
            continue
        # 缩进小于等于 def 缩进 → 函数结束（除非是装饰器）
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= def_indent and not stripped.startswith("@"):
            break
        body_lines.append(line)
        i += 1

    return "\n".join(body_lines) if body_lines else None


def _analyze_function(source_file: Path | None, func_name: str) -> set[str]:
    """分析特定函数的函数体，返回性能特征集合。

    比文件级分析更精确——避免把同文件其他函数的特征误归给工具。
    """
    if source_file is None or not source_file.exists():
        return set()

    try:
        content = source_file.read_text(encoding="utf-8")
    except OSError:
        return set()

    body = _extract_function_body(content, func_name)
    if body is None:
        return set()

    patterns: set[str] = set()

    if "subprocess" in body:
        patterns.add("subprocess")

    if re.search(r"(read_text|read_bytes|iterdir|rglob)", body):
        patterns.add("file_io")

    if re.search(r"(cache|_CACHE|functools\.lru_cache|@lru_cache|_read_cached)", body):
        patterns.add("has_caching")

    return patterns


def _analyze_source(source_file: Path | None) -> set[str]:
    """扫描全源文件，返回识别的性能特征集合（文件级模式）。"""
    if source_file is None or not source_file.exists():
        return set()

    try:
        content = source_file.read_text(encoding="utf-8")
    except OSError:
        return set()

    patterns: set[str] = set()

    # 网络 I/O — 整个模块的模式
    if re.search(r"(requests\.|urllib|httpx|aiohttp|self\.client\.)", content):
        patterns.add("network")

    # 缓存 — 函数或模块级均可
    if re.search(r"(cache|_CACHE|functools\.lru_cache|@lru_cache|_read_cached)", content):
        patterns.add("has_caching")

    return patterns


def _suggest_subprocess(tool: str) -> str:
    """针对 subprocess 慢的个性化建议。"""
    if tool == "run_command":
        return (
            "run_command 耗时来自被调外部命令（pytest/ruff/git 等），"
            "subprocess 本身开销极小。优化方向：检查是否有不必要的重复调用；"
            "可缓存的结果（如 ruff check 同文件无改动）加缓存层。"
        )
    if tool == "run_python":
        return (
            "run_python 每次冷启动 Python 子进程（~200-500ms 开销）。"
            "建议：检查执行的代码是否有不必要的 import / 重计算；"
            "简单值计算可考虑用表达式替代子进程。"
        )
    return "外部进程耗时，检查是否可减少调用或缓存结果。"


# ═══════════════════════════════════════════════════════════════
# 展示格式化
# ═══════════════════════════════════════════════════════════════

_SEVERITY_ICONS = {"crit": "🔴", "warn": "🟡"}


def format_diagnostics(anomalies: list[dict]) -> str:
    """把诊断结果格式化成 agent 可读的消息文本。

    输入是 reflection 中的 anomalies 列表（含 raw detail），内部调用诊断。
    返回格式化的多行文本；空 anomalies 返回空串。
    """
    if not anomalies:
        return ""

    diagnostics = diagnose_anomalies(anomalies)
    lines = ["🔍 被动进化发现了以下值得关注的问题："]

    for a, d in zip(anomalies, diagnostics, strict=True):
        icon = _SEVERITY_ICONS.get(a.get("severity", ""), "⚪")
        lines.append(
            f"  {icon} [{d.anomaly_type}] {d.anomaly_detail}"
        )
        if d.root_pattern != "unknown":
            lines.append(f"     → 诊断: {d.likely_cause}")
            if d.suggested_action:
                lines.append(f"     → 建议: {d.suggested_action}")
            lines.append(f"     → 置信度: {d.confidence:.0%} | 可行动: {'是' if d.actionable else '否'}")
        else:
            lines.append(f"     → {d.suggested_action}")

    lines.append(
        "如果需要修复，可以说 '处理这些' 或 '看看第一个'。"
        "我会用 /evolve 机制分析、修改、测试、提交。"
    )
    return "\n".join(lines)
