"""长任务自主续跑：agent 推进 current.md 后，由 REPL 自主驱动下一步，直到它自己停。

与 outcome.py（/goal 闭环）并列的第二种任务循环：那个按"完成标准语义评分"驱动，
这个按 current.md 的"步骤标记"驱动（不评分——用户选定）。

设计要点（见对话 2026-06-13，用户："让他自己判断做不做"）：
  - 续跑条件 = agent 这一轮【自己推进了】current.md，不是"任务还有未完成步骤"。
    后者（旧补丁 has_unfinished）会在 agent 想停下问你时硬推"继续"，逼出自问自答。
  - 全程不注入"继续/不要停"这类 meta 指令（那正是 agent echo "继续做" 的来源）；
    驱动输入是任务内容框架，agent 执行它而不是 acknowledge 它。
  - 停由 agent 的判断触发，系统只 honor、不 puppet：
      停止推进（这一步没改 current.md）/ 标 [!][r]（要你拍板）/ 全 [x]（完成）。
    预算是兜底上限，不是主停因。
  - 不 import main：drive_turn 由调用方注入（同 run_outcome），避免环依赖。
"""

from __future__ import annotations

from collections.abc import Callable

from .params import RUNTIME
from .tasks import _BLOCKED_MARKERS as BLOCKED_MARKERS
from .tasks import _UNFINISHED_MARKERS as UNFINISHED_MARKERS
from .tasks import archive_current, read_current


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

    current.md 已由 make_task_context_message 每轮注入上下文，这里不重复贴清单，
    只给自主执行的姿态 + 停下问我的明确出口。
    """
    return (
        "[长任务·自主推进] 按你自己的判断做 current.md 的下一步，做完更新步骤标记"
        "（进度记在步骤后，如 47/250）。全部完成就归档。"
        "如果遇到必须我拍板才能继续的决策，把那一步标成 [!] 并写清要问我什么，然后停下——我会看到。"
        "常规的下一步不用征求许可。"
    )


def run_task_continuation(
    ctx,
    drive_turn: Callable[[object, str], object],
    on_status: Callable[[str], None] = lambda _s: None,
    budget: int | None = None,
) -> None:
    """在一次推进了 current.md 的回合之后，自主驱动后续步骤，直到 agent 自己停。

    调用前提（由 REPL 保证）：上一回合 made_task_progress 为真。
    drive_turn(ctx, prompt) 跑一整轮真实 process_turn 管线（Esc 可中断，由调用方接）。
    """
    budget = budget if budget is not None else RUNTIME.task_continue_budget
    for _ in range(budget):
        text = read_current()
        if not text.strip():
            return  # 任务被清空/归档
        if not _has(text, UNFINISHED_MARKERS + BLOCKED_MARKERS):
            on_status(archive_current())
            on_status("✅ 长任务全部完成，已归档。")
            return
        if _has(text, BLOCKED_MARKERS):
            on_status("⏸ 任务里有需要你拍板的步骤（[!]/[r]），已停下——见上方任务清单。")
            return
        before = text
        drive_turn(ctx, _drive_prompt())
        if read_current() == before:
            on_status("⏸ 这一步没有推进任务清单，已停下交还你。")
            return
    on_status(f"⏸ 已自主推进 {budget} 步（预算上限），暂停。要继续就说一声。")
