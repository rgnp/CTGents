"""渐进安全模型 — 覆盖率门禁控制文件修改权限。

覆盖率越高 → 解锁越多文件可修改。
agent 可以主动添加测试来提升覆盖率，从而解锁更多修改目标。

Tier 定义：
  tier_0_open:     src/tools/*, plugins/  →  0% (始终开放)
  tier_1_config:   配置/会话/命令模块      → 45%
  tier_2_core:     核心 LLM/缓存/安全模块   → 60%
  tier_3_critical: 关键模块(guard, main)   → 75%
  tier_4_watchdog: watchdog.py            → 100% (永不解锁)
"""

import json
import re
import subprocess
import time
from enum import Enum
from pathlib import Path

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
    "tier_4_watchdog": {
        "patterns": [
            "src/watchdog.py",
        ],
        "threshold": 1.0,
        "description": "Watchdog — 不可修改",
    },
}

# 缓存覆盖率结果（60s TTL，避免重复跑 pytest --cov）
_coverage_cache: tuple[float, dict[str, float], float] | None = None


def clear_cache() -> None:
    """清除覆盖率缓存（代码修改后调用）。"""
    global _coverage_cache
    _coverage_cache = None


def _match_pattern(filepath: str, pattern: str) -> bool:
    """简单 glob 匹配：支持 * 和 **。"""
    fp = filepath.replace("\\", "/")
    pat = pattern.replace("\\", "/")

    # 构建正则：** → 匹配任意深度，* → 匹配单层非 /
    regex_parts = []
    i = 0
    while i < len(pat):
        if pat[i:i+2] == "**":
            regex_parts.append(r".*")
            i += 2
        elif pat[i] == "*":
            regex_parts.append(r"[^/]*")
            i += 1
        else:
            # 转义非特殊字符
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
        return None  # 不在项目内
    rel_str = str(rel).replace("\\", "/")

    for tier_name, tier_info in FILE_TIERS.items():
        for pattern in tier_info["patterns"]:
            if _match_pattern(rel_str, pattern):
                return tier_name, tier_info
    return None


def _run_coverage() -> tuple[float, dict[str, float]]:
    """运行 pytest --cov 获取覆盖率数据。返回 (total_pct, {filepath: pct})。"""
    try:
        result = subprocess.run(
            ["py", "-m", "pytest", "--cov=src", "--cov-report=json", "--cov-report=term",
             "-p", "no:cacheprovider", "-q", "--tb=no"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=120,
            encoding="utf-8", errors="replace",
        )
        # 解析 coverage.json
        cov_json_path = PROJECT_ROOT / "coverage.json"
        if cov_json_path.exists():
            data = json.loads(cov_json_path.read_text(encoding="utf-8"))
            total_pct = data.get("totals", {}).get("percent_covered", 0.0) / 100.0
            file_cov: dict[str, float] = {}
            for fpath, finfo in data.get("files", {}).items():
                pct = finfo.get("summary", {}).get("percent_covered", 0.0) / 100.0
                file_cov[fpath] = pct
            return total_pct, file_cov
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return 0.0, {}


def get_overall_coverage() -> float:
    """获取总体覆盖率（0.0-1.0），带缓存。"""
    global _coverage_cache
    now = time.time()
    if _coverage_cache is not None and (now - _coverage_cache[2]) < 60:
        return _coverage_cache[0]
    total, files = _run_coverage()
    _coverage_cache = (total, files, now)
    return total


def get_file_coverage(filepath: str) -> float | None:
    """获取单个文件的覆盖率（0.0-1.0），无数据返回 None。"""
    _, files = _run_coverage() if _coverage_cache is None else (_coverage_cache[0], _coverage_cache[1])
    fp = str(Path(filepath).resolve())
    # coverage.json 中的 key 是相对路径
    try:
        rel = str(Path(fp).relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return None
    return files.get(rel)


def can_modify(filepath: str) -> tuple[bool, str]:
    """检查 agent 是否有权限修改指定文件。返回 (allowed, reason)。"""
    tier = _get_tier(filepath)
    if tier is None:
        return False, f"文件不在项目范围内: {filepath}"

    tier_name, tier_info = tier
    threshold = tier_info["threshold"]

    if threshold >= 1.0:
        return False, f"{filepath} 位于 {tier_name}（{tier_info['description']}），不可修改"

    coverage = get_overall_coverage()

    if coverage >= threshold:
        return True, f"{filepath} ({tier_name}) — 覆盖率 {coverage:.0%} >= {threshold:.0%}，允许修改"
    else:
        gap = threshold - coverage
        return False, (
            f"{filepath} ({tier_name}) — 当前覆盖率 {coverage:.0%}，"
            f"需要 {threshold:.0%}（差 {gap:.0%}）。"
            f"添加测试提升覆盖率后可解锁。"
        )


def get_access_level(filepath: str) -> AccessLevel:
    """确定文件的访问级别。"""
    tier = _get_tier(filepath)
    if tier is None:
        return AccessLevel.READ_ONLY

    _tier_name, tier_info = tier
    if tier_info["threshold"] >= 1.0:
        return AccessLevel.RESTRICTED

    coverage = get_overall_coverage()
    if coverage >= tier_info["threshold"]:
        return AccessLevel.WRITE_WITH_BACKUP
    return AccessLevel.READ_ONLY


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
        if coverage >= tier_info["threshold"]:
            modifiable.append(rel)

    return sorted(modifiable)


def get_tier_summary() -> str:
    """生成可读的各层解锁状态摘要。"""
    coverage = get_overall_coverage()
    lines = ["## 覆盖率门禁状态", f"当前总体覆盖率: {coverage:.0%}", ""]

    for tier_name in ["tier_0_open", "tier_1_config", "tier_2_core",
                       "tier_3_critical", "tier_4_watchdog"]:
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


def suggest_tests_to_unlock(target_file: str) -> str:
    """建议需要添加哪些测试才能修改目标文件。"""
    tier = _get_tier(target_file)
    if tier is None:
        return f"{target_file} 不在项目范围内，无法分析。"

    tier_name, tier_info = tier
    coverage = get_overall_coverage()

    if coverage >= tier_info["threshold"]:
        return f"{target_file} ({tier_name}) 已解锁，可直接修改。"

    gap = tier_info["threshold"] - coverage
    # 找未覆盖的模块
    _, file_cov = (_coverage_cache[0], _coverage_cache[1]) if _coverage_cache else _run_coverage()
    uncovered: list[str] = []
    for py_file in PROJECT_ROOT.glob("src/**/*.py"):
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if file_cov.get(rel, 0.0) < 0.3:
            uncovered.append(rel)

    suggestion = (
        f"要修改 {target_file} ({tier_name})，需要覆盖率从 {coverage:.0%} 提升到 {tier_info['threshold']:.0%}（差 {gap:.0%}）。\n"
    )
    if uncovered:
        suggestion += f"以下文件覆盖率较低，可优先添加测试：\n"
        for f in sorted(uncovered)[:10]:
            fc = file_cov.get(f, 0.0)
            suggestion += f"  - {f}（当前 {fc:.0%}）\n"
    else:
        suggestion += "所有文件覆盖率都较高，可针对具体模块编写更深入的测试。\n"

    return suggestion


def get_unlocked_count() -> dict[str, int]:
    """返回各 tier 的已解锁文件数。"""
    coverage = get_overall_coverage()
    counts: dict[str, int] = {}
    for tier_name, tier_info in FILE_TIERS.items():
        count = 0
        for py_file in PROJECT_ROOT.glob("src/**/*.py"):
            t = _get_tier(str(py_file))
            if t and t[0] == tier_name and coverage >= tier_info["threshold"]:
                count += 1
        counts[tier_name] = count
    return counts
