"""自愈系统 — 运行时崩溃检测 + 自动回滚 + 上下文注入。

闭环流程：
  崩溃 → 解析 traceback → 定位 src/ 中肇事文件
       → 检查是否有编辑前备份（_backup 机制）
       → 有则自动回滚 → 构建崩溃报告
       → 注入 LLM 上下文 → 重试

保护：本模块被标记为受保护文件，agent 无法通过 write_file/edit_file_lines 修改。
"""

import logging
import re
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

# 本文件路径（用于自我保护）
_GUARD_FILE = Path(__file__).resolve()
# 受保护文件列表（agent 不能修改）
PROTECTED_FILES: frozenset[str] = frozenset({
    str(_GUARD_FILE),
})


def analyze_crash(exc_type: type, exc_value: BaseException, exc_tb) -> dict:
    """分析崩溃，返回恢复方案。

    Returns:
        {"recoverable": bool,
         "traceback": str,
         "culprit_files": list[str],        # traceback 中涉及的 src/ 文件
         "rollback_candidates": dict}        # {filepath: backup_path}
    """
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text = "".join(tb_lines)

    # 1. 从 traceback 中提取 src/ 下的文件路径
    culprit_files = _extract_src_files(tb_text)
    if not culprit_files:
        return {"recoverable": False, "traceback": tb_text,
                "culprit_files": [], "rollback_candidates": {}}

    # 2. 查找哪些文件有最近的备份（说明被 agent 修改过）
    rollback_candidates = _find_backups(culprit_files)

    return {
        "recoverable": len(rollback_candidates) > 0,
        "traceback": tb_text,
        "culprit_files": list(culprit_files),
        "rollback_candidates": rollback_candidates,
    }


def _extract_src_files(tb_text: str) -> set[str]:
    """从 traceback 中提取 src/ 目录下的 .py 文件路径。"""
    # 匹配 traceback 中的 File ".../src/...py" 模式
    pattern = re.compile(r'File "([^"]*src[^"]*\.py)"')
    matches = pattern.findall(tb_text)
    return {m for m in matches if Path(m).exists()}


def _find_backups(filepaths: set[str]) -> dict[str, str]:
    """查找文件的最近备份。返回 {filepath: backup_path}。"""
    from .tools.file import _list_backups

    candidates = {}
    for fp in filepaths:
        if fp == str(_GUARD_FILE):
            continue  # 不回滚 guard 本身
        backups = _list_backups(Path(fp))
        if backups:
            # 最近一次备份（最新的）
            candidates[fp] = str(backups[0])
    return candidates


def execute_rollback(rollback_candidates: dict[str, str]) -> list[str]:
    """执行回滚：将备份文件恢复到原路径。返回成功恢复的文件列表。"""
    import shutil

    restored = []
    for filepath, backup_path in rollback_candidates.items():
        try:
            shutil.copy2(backup_path, filepath)
            restored.append(filepath)
            logger.info("自愈: 已回滚 %s ← %s", filepath, backup_path)
        except OSError as e:
            logger.error("自愈: 回滚失败 %s: %s", filepath, e)
    return restored


def build_report(analysis: dict, restored: list[str]) -> str:
    """构建注入 LLM 上下文的崩溃报告。"""
    tb_text = analysis.get("traceback", "")
    # 取 traceback 的最后 3000 字符（包含最关键的错误信息和堆栈帧）
    tb_short = tb_text[-3000:] if len(tb_text) > 3000 else tb_text

    parts = [
        "## ⚠️ 系统崩溃报告",
        "",
        "上一次对话中的代码修改导致程序崩溃。以下是诊断信息：",
        "",
        "### 崩溃堆栈",
        "```",
        tb_short.strip(),
        "```",
    ]

    if restored:
        parts.append(f"\n### 已自动回滚\n以下文件已恢复到修改前的状态：")
        for f in restored:
            parts.append(f"  - {f}")
        parts.append("\n**请分析崩溃原因，换一种方式重新实现。**")
        parts.append("不要重复上次完全相同的修改——那会导致同样的崩溃。")

    return "\n".join(parts)


# ── 自我保护 ──

def is_protected(filepath: str | Path) -> bool:
    """检查文件是否受保护（不允许 agent 修改）。"""
    try:
        resolved = str(Path(filepath).resolve())
    except (OSError, ValueError):
        return False
    return resolved in PROTECTED_FILES


def register_protected(filepath: str) -> None:
    """动态注册额外的受保护文件。"""
    global PROTECTED_FILES
    PROTECTED_FILES = PROTECTED_FILES | {str(Path(filepath).resolve())}
