"""自主神经系统 — 第4层：自主决策+执行编排。

搭在现有三层（diagnostics→gaps→outcome）之上，把 evolution_runner 的
记录能力接上真实工作流：检测→决策→执行→验证→提交→存档→学习。

不替代 agent 判断——只提供轨道和闸门。agent 决定"修不修"和"怎么修"，
本模块负责"修得安全"和"修完不忘"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .evolution_runner import (
    EvolutionRun,
    RunnerStatus,
    complete_evolution_run,
    load_active_evolution_run,
    start_evolution_run,
)
from .gaps import (
    Gap,
    _make_fix_prompt,
    detect_all_gaps,
    get_gap_by_index,
    get_last_report,
)

# ═══════════════════════════════════════════════════════════════
# 心跳检测
# ═══════════════════════════════════════════════════════════════


def pulse() -> str | None:
    """一次自主心跳：检测→优选→返回可行动方向。不自动执行（安全边界：agent 决策权）。

    返回 None 表示无值得关注的方向；返回文本可直接注入 volatile 上下文。
    """
    # 如果已有一个活跃的 evolution run，先完成它
    active = load_active_evolution_run()
    if active is not None:
        return _active_run_nudge(active)

    report = detect_all_gaps()
    if not report.gaps:
        return None

    # 只挑 actionable=True 且 confidence > 0.5 的
    candidates = [g for g in report.gaps if g.actionable and g.confidence > 0.5]
    if not candidates:
        return None

    best = candidates[0]  # detect_all_gaps 已按优先级排序
    prompt = _make_fix_prompt(best, 1)

    return (
        "🔍 自主心跳检测到可改进方向：\n\n"
        + prompt
        + "\n\n──\n"
        + "说 '处理' 开始自主进化，或说 '跳过' 继续当前对话。"
    )


def _active_run_nudge(run: EvolutionRun) -> str:
    """有未完成的进化 run 时，提醒 agent 继续。"""
    return (
        f"⚠️ 检测到未完成的进化 run（{run.run_id}）：\n"
        f"  目标: {run.goal}\n"
        f"  阶段: {run.phase}\n"
        f"  状态: {run.status}\n\n"
        "说 '继续进化' 恢复此 run，或 '放弃进化' 标记为 stopped。"
    )


# ═══════════════════════════════════════════════════════════════
# 执行轨道
# ═══════════════════════════════════════════════════════════════


@dataclass
class PulseDecision:
    """一次自主决策的结果。"""

    accepted: bool
    gap: Gap | None = None
    run_id: str = ""
    run_summary: str = ""


def accept(gap_index: int = 1) -> PulseDecision:
    """Agent 决定修复某个 gap。启动 evolution run 并返回决策。"""
    report = get_last_report()
    gap = get_gap_by_index(gap_index) if report else None
    if not gap:
        return PulseDecision(accepted=False)

    run_start = start_evolution_run(gap.detail)
    return PulseDecision(
        accepted=True,
        gap=gap,
        run_id=run_start.run.run_id,
        run_summary=run_start.summary,
    )


def decline() -> PulseDecision:
    """Agent 决定跳过当前 gap。"""
    return PulseDecision(accepted=False)


def complete(
    run_id: str,
    files_changed: list[str],
    success: bool = True,
    note: str = "",
) -> str:
    """进化完成：关闭 run，返回摘要。进化档案由 _archive_run 自动写入。"""
    status = RunnerStatus.PASSED if success else RunnerStatus.FAILED

    try:
        run = complete_evolution_run(run_id, status, note)
    except (FileNotFoundError, ValueError) as e:
        return f"关闭进化 run 失败: {e}"

    duration = _calc_duration(run)
    return (
        f"进化{'成功' if success else '失败'}: {run.run_id}\n"
        f"  目标: {run.goal[:100]}\n"
        f"  修改文件: {files_changed}\n"
        f"  耗时: {duration:.0f}ms\n"
        f"  备注: {note}"
    )


def abort(run_id: str, reason: str = "") -> str:
    """放弃当前进化 run。"""
    try:
        run = complete_evolution_run(run_id, RunnerStatus.STOPPED, reason)
        return f"已放弃进化 run: {run.run_id}"
    except (FileNotFoundError, ValueError) as e:
        return f"放弃进化 run 失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 上下文注入（供 main.py 调用）
# ═══════════════════════════════════════════════════════════════


def make_context_message() -> dict | None:
    """生成可供 volatile 上下文注入的消息。None = 无需注入。

    由 main.py 的 _append_volatile_context 调用，
    在每轮开始时注入自主心跳结果。
    """
    try:
        nudge = pulse()
    except Exception:
        return None
    if not nudge:
        return None
    return {"role": "system", "content": nudge, "_volatile": True, "_autonomic_pulse": True}


def strip_autonomic_messages(ctx_log: list[dict]) -> None:
    """清除上一轮的 autonomic 消息，防累积。"""
    ctx_log[:] = [m for m in ctx_log if not m.get("_autonomic_pulse")]


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════


def _calc_duration(run: EvolutionRun) -> float:
    """从 run 的创建/更新时间戳估算耗时（毫秒）。"""
    try:
        created = time.mktime(time.strptime(run.created_at[:19], "%Y-%m-%dT%H:%M:%S"))
        updated = time.mktime(time.strptime(run.updated_at[:19], "%Y-%m-%dT%H:%M:%S"))
        return (updated - created) * 1000
    except (ValueError, IndexError):
        return 0.0
