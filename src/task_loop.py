"""长任务自主续跑 + 动态重规划：每 N 步跑一次计划审查，agent 可用 update_plan 重写计划。

设计要点（见对话 2026-06-13，用户："让他自己判断做不做"）：
  - 续跑条件 = agent 这一轮【自己推进了】current.md，不是"任务还有未完成步骤"。
    后者（旧补丁 has_unfinished）会在 agent 想停下问你时硬推"继续"，逼出自问自答。
  - 全程不注入"继续/不要停"这类 meta 指令（那正是 agent echo "继续做" 的来源）；
    驱动输入是任务内容框架，agent 执行它而不是 acknowledge 它。
  - 停由 agent 的判断触发，系统只 honor、不 puppet：
      停止推进（这一步没改 current.md）/ 标 [!][r]（要你拍板）/ 全 [x]（完成）。
    预算是兜底上限，不是主停因。
  - 不 import main：drive_turn 由调用方注入，避免环依赖。
  - 动态重规划（F）：每 planning_interval 步，先注入计划审查 prompt，agent 可以
    用 update_plan 工具重写计划结构（增删改步骤/重排序），审查本身也算一步预算。
"""

from __future__ import annotations

from collections.abc import Callable

from .params import RUNTIME
from .params import TASK as TASK_PARAMS
from .tasks import _BLOCKED_MARKERS as BLOCKED_MARKERS
from .tasks import _UNFINISHED_MARKERS as UNFINISHED_MARKERS
from .tasks import archive_current, get_task_short_status, read_current


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def made_task_progress(before: str, after: str) -> bool:
    """这一轮是否在 current.md 上有实质推进：内容变了、仍有活步骤、且没标阻塞。

    REPL 用它判断"agent 是不是自己在做这个任务"→ 决定要不要自主续驱动下一步。
    新建任务（before 空）不算"推进"——建计划不等于决定立刻执行，由 agent 下一步动手时再触发。
    """
    return (
        bool(before.strip())
        and bool(after.strip())
        and after != before
        and _has(after, UNFINISHED_MARKERS)
        and not _has(after, BLOCKED_MARKERS)
    )


def _drive_prompt() -> str:
    """自主续跑每步的驱动输入：任务内容框架，不是"继续"meta 指令。

    只给自主执行的姿态 + 显式停止信号的出口（task_done/need_user 走工具，被循环认账，
    取代"在回复里说停"——后者会被自动续跑覆盖）。
    """
    return (
        "[长任务·自主推进] 按你自己的判断做 current.md 的下一步，做完更新步骤标记"
        "（进度记在步骤后，如 47/250）。\n"
        "停下的正式信号（别只在回复里说，否则会被自动续跑覆盖）：\n"
        "· 整个任务全部完成 → 把所有步骤标 [x] 并调 task_done\n"
        "· 需要我拍板/缺信息才能继续 → 调 need_user 写清要问什么（或把该步标 [!]）\n"
        "常规的下一步不用征求许可，直接做。"
    )


def _review_prompt() -> str:
    """计划审查的驱动输入：让 agent 停下来评估计划是否还匹配现实。

    不注入"继续"meta 指令——框架是"评估→决定→行动"，agent 自己决定是否需要
    用 update_plan 重写，还是继续推进。
    """
    return (
        "[长任务·计划审查] review 当前计划（current.md）是否还匹配实际情况。\n"
        "行动选项（三选一，按你的判断）：\n"
        "1. 计划仍然合适 → 直接执行下一步（标记步骤 [o] 或 [x]）\n"
        "2. 计划需要调整 → 用 update_plan 工具重写步骤清单（增删改/重排序皆可）\n"
        "3. 方向完全不对 → 调 need_user 说明情况，让我一起判断\n"
        "注意：这是每 N 步一次的例行方向检查。"
    )


def _run_planning_review(
    ctx,
    drive_turn: Callable[[object, str], object],
    on_status: Callable[[str], None],
    review_budget: int,
) -> bool:
    """一次计划审查：让 agent 评估计划。返回 False 表示需要停（被中断/阻塞）。"""
    before = read_current()
    for _ in range(review_budget):
        text = read_current()
        if not text.strip():
            return True  # 任务已被清空，继续会退出
        if _has(text, BLOCKED_MARKERS):
            return False  # 阻塞标记 → 停
        drive_turn(ctx, _review_prompt())
        sig = getattr(ctx, "control_signal", None)
        if sig == "interrupted":
            return False
        if sig == "need_user":
            return False
        if sig == "task_done":
            return False
        # 如果 plan 变了（agent 调了 update_plan），审查目的已达到
        if read_current() != before:
            on_status(f"🔁 计划已调整：{get_task_short_status()}")
            return True
    return True  # 审查预算用完但没事，继续执行


def run_task_continuation(
    ctx,
    drive_turn: Callable[[object, str], object],
    on_status: Callable[[str], None] = lambda _s: None,
    budget: int | None = None,
    stall_limit: int | None = None,
    planning_interval: int | None = None,
    plan_review_budget: int | None = None,
) -> None:
    """有未完成步骤就自主驱动下一步，直到 agent 显式喊停 / 全完成 / 卡死 / 预算上限。

    新功能（vs 旧版）：
    - planning_interval：每 N 步跑一次计划审查（动态重规划 F）
    - 审查步骤计入 budget（防无限审查螺旋）

    停的优先级（显式 > 状态 > 兜底）：
      1. agent 调 need_user → 把问题交还用户，停。
      2. agent 调 task_done → 声明完成（全 [x] 则归档），停。
      3. current.md 有 [!]/[r] 阻塞标记 → 需用户拍板，停。
      4. 没有 [ ]/[o] 活跃未完成步骤 → 全完成归档，停。
      5. 连续 stall_limit 步没推进清单 → 疑卡死/需用户，停（容一次忘勾标记的宽限）。
      6. 预算 budget 步用尽 → 暂停兜底。
    drive_turn(ctx, prompt) 跑一整轮真实 process_turn 管线（Esc 可中断，由调用方接）。
    控制信号经 ctx.control_signal/payload 传回（run_conversation 每轮复位+按需置位）。
    """
    budget = budget if budget is not None else RUNTIME.task_continue_budget
    stall_limit = stall_limit if stall_limit is not None else RUNTIME.task_stall_limit
    planning_interval = planning_interval if planning_interval is not None else TASK_PARAMS.planning_interval
    plan_review_budget = plan_review_budget if plan_review_budget is not None else TASK_PARAMS.plan_review_budget

    no_progress = 0

    for step_num in range(1, budget + 1):
        text = read_current()
        if not text.strip():
            return  # 任务被清空/归档
        if _has(text, BLOCKED_MARKERS):
            on_status("⏸ 任务里有需要你拍板的步骤（[!]/[r]），已停下——见上方任务清单。")
            return
        if not _has(text, UNFINISHED_MARKERS):
            on_status(archive_current())
            on_status("✅ 长任务全部完成，已归档。")
            return

        # ── 计划审查：每 planning_interval 步，先 review 再继续 ──
        if planning_interval > 0 and step_num % planning_interval == 0:
            on_status(f"🔎 第 {step_num} 步：计划审查（每 {planning_interval} 步一次）")
            if not _run_planning_review(ctx, drive_turn, on_status, plan_review_budget):
                return

        # ── 执行步骤 ──
        before = read_current()
        drive_turn(ctx, _drive_prompt())

        sig = getattr(ctx, "control_signal", None)
        if sig == "interrupted":
            on_status("⏹ 已被你截停，长任务续跑停下——上下文已保留，要接着干就按 Enter。")
            return
        if sig == "need_user":
            q = getattr(ctx, "control_payload", None) or ""
            on_status(f"⏸ agent 需要你拍板：{q}" if q else "⏸ agent 请你拍板，已停下。")
            return
        if sig == "task_done":
            if not _has(read_current(), UNFINISHED_MARKERS + BLOCKED_MARKERS):
                on_status(archive_current())
            s = getattr(ctx, "control_payload", None) or ""
            on_status(f"✅ agent 声明任务完成。{s}".rstrip())
            return

        if read_current() == before:
            no_progress += 1
            if no_progress >= stall_limit:
                on_status(
                    f"⏸ 连续 {no_progress} 步没推进任务清单，可能卡住或需要你——已停下交还你。"
                )
                return
        else:
            no_progress = 0

    from .tasks import get_task_progress_line
    progress = get_task_progress_line()
    tail = f"（当前进度：{progress}）" if progress else ""
    on_status(f"⏸ 已自主推进 {budget} 步（自主上限），先停下来跟你对一次{tail}。要接着干就说一声。")
