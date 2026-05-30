"""Auto Mode 安全系统：工具安全等级 + 自动/手动模式 + 会话信任。

三级安全等级：
  SAFE      — 只读，无副作用，自动放行
  RISKY     — 修改文件但可逆，手动模式需确认
  DANGEROUS — 破坏性/不可逆，始终需确认

两种模式：
  manual (默认) — 需要确认 RISKY + DANGEROUS
  auto          — SAFE 自执行，RISKY 继续询问，DANGEROUS 继续询问
"""

from __future__ import annotations

import enum

# ═══════════════════════════════════════════════════════════════
# 安全等级
# ═══════════════════════════════════════════════════════════════

class SafetyLevel(enum.Enum):
    SAFE = "safe"         # 只读，无副作用
    RISKY = "risky"       # 修改文件但可逆
    DANGEROUS = "dangerous"  # 破坏性/不可逆


# ═══════════════════════════════════════════════════════════════
# 每个工具的安全等级定义
# ═══════════════════════════════════════════════════════════════

# 内置工具 + discover/plugin_mgr 工具
TOOL_SAFETY: dict[str, SafetyLevel] = {
    # ── SAFE：只读，无副作用 ──
    "search_web":      SafetyLevel.SAFE,
    "read_page":       SafetyLevel.SAFE,
    "read_file":       SafetyLevel.SAFE,
    "rag_query":      SafetyLevel.SAFE,
    "rag_status":     SafetyLevel.SAFE,
    "rag_index":      SafetyLevel.RISKY,  # 写文件到 .rag-index/
    "read_file_lines": SafetyLevel.SAFE,
    "list_files":      SafetyLevel.SAFE,
    "count_lines":     SafetyLevel.SAFE,
    "grep_code":       SafetyLevel.SAFE,
    "scan_project":    SafetyLevel.SAFE,
    "check_project":   SafetyLevel.SAFE,
    "git_status":      SafetyLevel.SAFE,
    "git_diff":        SafetyLevel.SAFE,
    "git_log":         SafetyLevel.SAFE,
    "git_branch":      SafetyLevel.SAFE,
    "discover":        SafetyLevel.SAFE,
    "plugin_spec":     SafetyLevel.SAFE,
    "list_plugins":    SafetyLevel.SAFE,
    "think":           SafetyLevel.SAFE,
    "recall":          SafetyLevel.SAFE,
    "generate_agents_md": SafetyLevel.SAFE,

    # ── RISKY：修改文件但可逆 ──
    "write_file":      SafetyLevel.RISKY,
    "edit_file_lines": SafetyLevel.RISKY,
    "undo_edit":       SafetyLevel.RISKY,
    "delete_file":     SafetyLevel.RISKY,
    "run_python":      SafetyLevel.RISKY,
    "run_command":     SafetyLevel.RISKY,
    "remember":        SafetyLevel.RISKY,
    "forget":          SafetyLevel.RISKY,
    "git_commit":      SafetyLevel.RISKY,
    "git_pr":          SafetyLevel.RISKY,
    "install_plugin":  SafetyLevel.RISKY,

    # ── DANGEROUS：破坏性/不可逆 ──
    "git_push":        SafetyLevel.DANGEROUS,
}

# 已知但未显式分配的工具默认用 RISKY
_DEFAULT_LEVEL = SafetyLevel.RISKY


# ═══════════════════════════════════════════════════════════════
# 状态
# ═══════════════════════════════════════════════════════════════

_mode: str = "manual"           # "manual" | "auto"
_session_trust: set[str] = set()   # 当前会话信任的工具名


def get_mode() -> str:
    """返回当前模式：manual / auto。"""
    return _mode


def set_mode(mode: str) -> tuple[bool, str]:
    """切换模式。返回 (是否成功, 消息)。"""
    m = mode.lower()
    if m not in ("manual", "auto"):
        return False, f"无效模式: {mode}。可选: manual, auto"
    global _mode
    _mode = m
    return True, f"已切换到 {m} 模式"


def get_safety_level(tool_name: str) -> SafetyLevel:
    """获取工具的安全等级。"""
    return TOOL_SAFETY.get(tool_name, _DEFAULT_LEVEL)


# ═══════════════════════════════════════════════════════════════
# 会话信任
# ═══════════════════════════════════════════════════════════════

def trust_tool(tool_name: str) -> str:
    """信任一个工具（本会话内自动放行）。"""
    _session_trust.add(tool_name)
    level = get_safety_level(tool_name).value
    return f"已信任 {tool_name}（{level}），本会话内自动放行"


def revoke_trust(tool_name: str) -> str:
    """取消信任。"""
    _session_trust.discard(tool_name)
    return f"已取消信任 {tool_name}"


def is_trusted(tool_name: str) -> bool:
    """检查工具是否被信任。"""
    return tool_name in _session_trust


def list_trusted() -> str:
    """列出当前会话信任的工具。"""
    if not _session_trust:
        return "当前会话无信任工具"
    levels = {tool: get_safety_level(tool).value for tool in sorted(_session_trust)}
    lines = ["本会话信任的工具："]
    for tool, level in levels.items():
        lines.append(f"  ✅ {tool}（{level}）")
    return "\n".join(lines)


def clear_trust() -> str:
    """清空所有会话信任。"""
    _session_trust.clear()
    return "已清空所有会话信任"


# ═══════════════════════════════════════════════════════════════
# 安全检查核心
# ═══════════════════════════════════════════════════════════════

def check_tool(tool_name: str) -> str:
    """检查工具是否可以执行。

    Returns:
        "allow"   — 放行，无需确认
        "confirm" — 需要用户手动确认
        "block"   — 被安全策略禁止
    """
    # 信任列表中的工具直接放行
    if tool_name in _session_trust:
        return "allow"

    level = get_safety_level(tool_name)

    if level == SafetyLevel.SAFE:
        return "allow"

    if level == SafetyLevel.DANGEROUS:
        # 危险操作即使在 auto 模式也需要确认
        return "confirm"

    # RISKY 工具
    if _mode == "auto":
        # auto 模式下 risky 也放行
        return "allow"
    else:
        return "confirm"


def get_mode_summary() -> str:
    """返回模式摘要，用于注入 system prompt。"""
    trusted = f"，信任 {len(_session_trust)} 个工具" if _session_trust else ""
    return f"安全模式: {_mode.upper()}{trusted}"


def format_tool_safety(tool_name: str) -> str:
    """格式化工具的安全信息。"""
    level = get_safety_level(tool_name).value
    trusted = " ✅" if tool_name in _session_trust else ""
    return f"{tool_name} [{level}]{trusted}"
