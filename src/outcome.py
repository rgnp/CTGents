"""任务闭环:目标 → 执行 → 评分 → 修订(outcome loop)。

把"反向验证"从 disposition 变成控制流:任务开始时写定完成标准,worker(现有
process_turn 管线)干活,**干净上下文**的评分步对照标准逐条打分,不达标就把
差距清单作为下一轮输入回灌,直到达标或到轮数上限。

评分者隔离是本模块的核心不变量:评分 payload 只含目标/标准/交付物,绝不含
worker 的对话 log——否则 worker 能用自己的叙述给自己的交付背书
(citation_audit 不收 assistant 自己的话,同一洞察的任务级放大)。

分工边界:可机械判定的标准(测试退出码/lint)归机械层(completion_audit/
pre-commit),评分环只管语义标准——判断题给 LLM 评分者,不机械化成规则。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .params import OUTCOME

logger = logging.getLogger(__name__)

# worker 每轮交付物在最终回复里的体量预期;结构性措辞留本模块。
_GRADER_SYSTEM = (
    "你是严格的交付评审。只依据给定的完成标准逐条评判交付物,"
    "不脑补交付物里没有的证据;拿不准就判不通过并写明缺什么。"
    '只输出 JSON:{"criteria":[{"criterion":"...","pass":true,"gap":"..."}],'
    '"satisfied":true,"summary":"..."}。'
    "pass 仅当交付物中有可见证据;satisfied 仅当全部标准 pass。"
)


@dataclass
class OutcomeSpec:
    """一次任务闭环的规格:目标 + 逐条可判定的完成标准。"""

    goal: str
    criteria: list[str]
    deliverable_path: str = ""   # 可选:交付物文件(评分时机械读盘,比转述可信)
    max_iterations: int = OUTCOME.max_iterations


@dataclass
class GradeResult:
    satisfied: bool
    gaps: list[str] = field(default_factory=list)  # 未达标项:"标准 — 缺什么"
    summary: str = ""
    raw: str = ""


@dataclass
class OutcomeResult:
    satisfied: bool
    iterations: int
    grades: list[GradeResult] = field(default_factory=list)


def parse_goal(text: str) -> OutcomeSpec | None:
    """解析 /goal 语法:`目标 || 标准1 | 标准2 [>> 交付文件路径]`。缺标准返回 None。"""
    if "||" not in text:
        return None
    goal, _, crit_part = text.partition("||")
    path = ""
    if ">>" in crit_part:
        crit_part, _, path = crit_part.partition(">>")
    criteria = [c.strip() for c in crit_part.split("|") if c.strip()]
    if not goal.strip() or not criteria:
        return None
    return OutcomeSpec(goal=goal.strip(), criteria=criteria,
                       deliverable_path=path.strip())


def _grader_payload(spec: OutcomeSpec, deliverable: str) -> str:
    """评分输入:只含目标/标准/交付物——评分者隔离,worker log 决不进来。"""
    lines = [f"【任务目标】{spec.goal}", "【完成标准】"]
    lines += [f"{i}. {c}" for i, c in enumerate(spec.criteria, 1)]
    if spec.deliverable_path:
        p = Path(spec.deliverable_path)
        try:
            content = p.read_text(encoding="utf-8")
            lines.append(f"【交付文件 {spec.deliverable_path}】\n{content}")
        except OSError:
            lines.append(f"【交付文件 {spec.deliverable_path}】(读取失败:文件不存在或不可读)")
    lines.append(f"【worker 最终回复】\n{deliverable}")
    return "\n".join(lines)


def _parse_grade(raw: str) -> GradeResult:
    """解析评分 JSON;修不动则保守判不达标(评分坏了不能放行交付)。"""
    from .llm import _repair_json
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(_repair_json(text))
        except json.JSONDecodeError:
            return GradeResult(satisfied=False, summary="评分输出无法解析,保守判不达标",
                               gaps=["评分输出无法解析,请重新交付并简化呈现"], raw=raw)
    gaps = [f"{c.get('criterion', '?')} — {c.get('gap', '未说明')}"
            for c in data.get("criteria", []) if not c.get("pass")]
    return GradeResult(satisfied=bool(data.get("satisfied")) and not gaps,
                       gaps=gaps, summary=str(data.get("summary", "")), raw=raw)


def grade(spec: OutcomeSpec, deliverable: str) -> GradeResult:
    """干净上下文评分:独立 API 调用,无工具,与 worker 对话完全隔离。"""
    from .llm import AVAILABLE_MODELS, RETRYABLE
    backend = AVAILABLE_MODELS["pro"]
    messages = [{"role": "system", "content": _GRADER_SYSTEM},
                {"role": "user", "content": _grader_payload(spec, deliverable)}]
    last_err: Exception | None = None
    for attempt in range(OUTCOME.grader_retries + 1):
        try:
            content, _ = backend.chat_non_stream(messages, lambda _t: None, tools=None)
            return _parse_grade(content or "")
        except RETRYABLE as e:
            last_err = e
            time.sleep(OUTCOME.grader_retry_delay * (attempt + 1))
    logger.warning("评分调用失败(%s),保守判不达标", last_err)
    return GradeResult(satisfied=False, summary=f"评分调用失败:{last_err}",
                       gaps=["评分调用失败,请重试"])


def _worker_prompt(spec: OutcomeSpec, iteration: int, prev: GradeResult | None) -> str:
    """Worker 每轮输入:首轮交底目标+标准;后续只回灌差距清单(追加式,缓存友好)。"""
    if iteration == 1:
        lines = [f"【任务】{spec.goal}", "【完成标准(评分步将逐条对照)】"]
        lines += [f"{i}. {c}" for i, c in enumerate(spec.criteria, 1)]
        target = (f"把交付物写入 {spec.deliverable_path}(评分时直接读盘)"
                  if spec.deliverable_path else "在最终回复中完整呈现交付物")
        lines.append(f"做完后{target},并逐条自检标准。交付会被独立评审,够格才算完成。")
        return "\n".join(lines)
    lines = [f"评分未通过(第 {iteration - 1} 轮)。未达标项:"]
    lines += [f"- {g}" for g in (prev.gaps if prev else [])]
    lines.append("只针对差距修订,然后重新完整交付。")
    return "\n".join(lines)


def _last_assistant_text(log: list[dict]) -> str:
    """Worker 本轮最终回复(无 tool_calls 的最后一条 assistant)。"""
    for msg in reversed(log):
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            return msg.get("content") or ""
    return ""


def run_outcome(ctx, spec: OutcomeSpec,
                drive_turn: Callable[[object, str], str],
                on_status: Callable[[str], None] = lambda _s: None) -> OutcomeResult:
    """跑完整闭环。drive_turn(ctx, user_input) 由调用方注入(main.process_turn
    的偏函数)——本模块不 import main,避免环依赖;评分与 worker 彻底分离。
    """
    grades: list[GradeResult] = []
    prev: GradeResult | None = None
    for i in range(1, spec.max_iterations + 1):
        on_status(f"── 第 {i}/{spec.max_iterations} 轮交付 ──")
        drive_turn(ctx, _worker_prompt(spec, i, prev))
        deliverable = _last_assistant_text(ctx.log)
        on_status("── 评分中(干净上下文,独立评审) ──")
        result = grade(spec, deliverable)
        grades.append(result)
        if result.satisfied:
            on_status(f"评分通过:{result.summary}")
            return OutcomeResult(satisfied=True, iterations=i, grades=grades)
        on_status("评分未通过:\n" + "\n".join(f"  - {g}" for g in result.gaps))
        prev = result
    on_status(f"已到轮数上限({spec.max_iterations}),最终未达标项见上。")
    return OutcomeResult(satisfied=False, iterations=spec.max_iterations, grades=grades)
