"""长任务状态：tasks/current.md 的读取 / 判活 / 注入 / 归档。

current.md 是长任务的"指令镜子" + 进度账本。一个长任务（如"搜 250 次论文"）
装不进单个上下文窗口，必须跨会话分块续做。本模块让启动时若有未完成步骤就把
current.md 注入上下文（volatile、缓存安全），agent 每次开会话都看得见断点，
从未完成处接着做，而不是把计划烂在文件里。
"""

from __future__ import annotations

import contextlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import CORE_ROOT, TASKS_DIR, resolve_runtime_path

PROJECT_ROOT = CORE_ROOT
CURRENT_TASK_FILE = TASKS_DIR / "current.md"
AMBITIONS_FILE = TASKS_DIR / "ambitions.md"
ARCHIVE_DIR = TASKS_DIR / "archive"
# 步骤标记
_UNFINISHED_MARKERS = ("[ ]", "[o]", "[O]")  # 活跃未完成（has_unfinished 判活用）
_BLOCKED_MARKERS = ("[r]", "[R]", "[!]")     # 阻塞/需重试（task_loop 用：不触发续做）
_SLUG_FALLBACK = "task"
_ANCHOR_HEADING = "# 目标锚点"
_ACCEPTANCE_HEADING = "## 验收"
_ACCEPTANCE_RESULT_HEADING = "## 验收结果"
_STOP_HEADING = "## 停止条件"
_ACCEPTANCE_RULE_RE = re.compile(
    r"^\s*-\s+`(?P<kind>steps|file|command)(?::\s*(?P<value>.*?))?`\s*$",
    re.IGNORECASE,
)
_STRUCTURED_RULE_RE = re.compile(
    r"^\s*-\s+`(?P<kind>[^`:]+)(?::\s*(?P<value>.*?))?`\s*$",
)
_SHELL_META_CHARS = frozenset("&|;<>\r\n")
_ACCEPTANCE_COMMAND_TIMEOUT = 180
_MAX_TASK_BUDGET = 100
_MAX_STALL_LIMIT = 10
# maybe_suggest_task_nudge + reset_gaps_cache + _task_suggested 已删除（2026-06-24）：
# 建任务建议挂尾随挂尾机制废止后 dormant、生产侧零调用；连带会话级缓存全空，一并清掉。


@dataclass(frozen=True)
class AcceptanceSpec:
    """一条可确定性执行的任务验收规则。"""

    kind: str
    value: str = ""


@dataclass(frozen=True)
class TaskSpec:
    """从 current.md 派生的最小任务合同；不改变现有 Markdown 存储格式。"""

    goal: str
    acceptance: tuple[AcceptanceSpec, ...]
    acceptance_declared: bool
    stops: tuple[StopSpec, ...]
    stop_declared: bool
    stop_errors: tuple[str, ...]


@dataclass(frozen=True)
class StopSpec:
    """一条任务可配置停止边界。"""

    kind: str
    value: str


@dataclass(frozen=True)
class StopPolicy:
    """校验后的停止策略；None 表示沿用系统默认值。"""

    budget: int | None = None
    stall_limit: int | None = None
    deadline: datetime | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AcceptanceCheck:
    """一条验收规则的执行结果。"""

    spec: AcceptanceSpec
    passed: bool
    evidence: str


@dataclass(frozen=True)
class AcceptanceResult:
    """任务验收聚合结果。"""

    configured: bool
    passed: bool
    checks: tuple[AcceptanceCheck, ...]

    def render(self) -> str:
        """渲染给 Agent、用户和归档文件复用的验收证据。"""
        if not self.configured:
            return "未声明结构化验收规则（兼容旧任务）。"
        lines = ["验收通过。" if self.passed else "验收未通过："]
        for check in self.checks:
            mark = "✅" if check.passed else "❌"
            value = f": {check.spec.value}" if check.spec.value else ""
            lines.append(f"- {mark} {check.spec.kind}{value} — {check.evidence}")
        return "\n".join(lines)


def read_ambitions() -> str:
    """返回 ambitions.md 全文（去掉一级标题）。"""
    if not AMBITIONS_FILE.exists():
        return ""
    text = AMBITIONS_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    # 跳过一级标题行
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    # 两个分区都空 = 没有实质内容
    if not body or body == "## 你的目标\n\n_暂无。_\n\n## Agent 的目标\n\n_暂无。_":
        return ""
    return body


def _extract_anchor(text: str) -> str:
    """从 current.md 提取目标锚点：`# 目标锚点` 后第一行非空文本。"""
    lines = text.splitlines()
    in_anchor = False
    for line in lines:
        stripped = line.strip()
        if in_anchor:
            if stripped and not stripped.startswith("-") and not stripped.startswith("#"):
                return stripped
            break
        elif stripped == _ANCHOR_HEADING:
            in_anchor = True
    return "".strip()


def get_task_progress_line() -> str:
    """解析 current.md 步骤，返回一行进度，如 "📋 (2/5) ✅ S1 ✅ S2 🔄 S3 ⬜ S4"。"""
    steps = _parse_task_steps()
    if not steps:
        return ""
    done = sum(1 for s, _ in steps if s == "✅")
    total = len(steps)
    labels = [f"{s} {lbl[:30]}" for s, lbl in steps]
    progress = f"📋 ({done}/{total}) " + " ".join(labels)
    return _trim_progress(progress, labels, done, total)


def _parse_task_steps() -> list[tuple[str, str]]:
    """解析 current.md 步骤行，返回 (图标, 文本) 列表。"""
    text = read_current()
    if not text:
        return []
    steps: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            steps.append(("✅", stripped[5:].strip()))
        elif stripped.startswith("- [o]") or stripped.startswith("- [O]"):
            steps.append(("🔄", stripped[5:].strip()))
        elif stripped.startswith("- [ ]"):
            steps.append(("⬜", stripped[5:].strip()))
        elif stripped.startswith("- [r]"):
            steps.append(("🔁", stripped[5:].strip()))
    return steps


def task_steps() -> list[tuple[str, str]]:
    """公开访问器：当前任务步骤 [(图标, 文本)]，供 TUI live TODO 面板渲染。"""
    return _parse_task_steps()


def _trim_progress(progress: str, labels: list, done: int, total: int) -> str:
    """超过 200 字符时截断进度线，保留前 4 步 + 省略标记。"""
    if len(progress) <= 200:
        return progress
    short = f"📋 ({done}/{total}) " + " ".join(labels[:4])
    if len(labels) > 4:
        short += f" …(+{len(labels) - 4})"
    return short


def read_current() -> str:
    """返回 current.md 内容（已 strip）；不存在返回空串。"""
    if not CURRENT_TASK_FILE.exists():
        return ""
    return CURRENT_TASK_FILE.read_text(encoding="utf-8").strip()


def _section(text: str, heading: str) -> tuple[bool, list[str]]:
    """提取二级 Markdown section；遇到下一个同级或更高标题结束。"""
    lines = text.splitlines()
    found = False
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            found = True
            continue
        if found and re.match(r"^#{1,2}\s+", stripped):
            break
        if found:
            body.append(line)
    return found, body


def _goal_from_text(text: str) -> str:
    anchor = _extract_anchor(text)
    if anchor:
        return anchor
    found, body = _section(text, "## 目标")
    if found:
        return next((line.strip() for line in body if line.strip()), "")
    return ""


def _parse_stop_specs(text: str) -> tuple[bool, tuple[StopSpec, ...], tuple[str, ...]]:
    declared, body = _section(text, _STOP_HEADING)
    specs: list[StopSpec] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line in body:
        match = _STRUCTURED_RULE_RE.match(line)
        if not match:
            continue
        kind = match.group("kind").strip().lower()
        value = (match.group("value") or "").strip()
        if kind not in {"budget", "stall", "deadline"}:
            errors.append(f"未知停止条件：{kind}")
            continue
        if kind in seen:
            errors.append(f"停止条件重复：{kind}")
            continue
        seen.add(kind)
        specs.append(StopSpec(kind=kind, value=value))
    if declared and not specs and not errors:
        errors.append("已声明 `## 停止条件`，但没有结构化规则")
    return declared, tuple(specs), tuple(errors)


def parse_task_spec(text: str | None = None) -> TaskSpec:
    """从现有 Markdown 派生任务合同；没有验收区的旧任务保持 legacy 行为。"""
    source = read_current() if text is None else text.strip()
    declared, body = _section(source, _ACCEPTANCE_HEADING)
    stop_declared, stops, stop_errors = _parse_stop_specs(source)
    specs: list[AcceptanceSpec] = []
    for line in body:
        match = _ACCEPTANCE_RULE_RE.match(line)
        if not match:
            continue
        kind = match.group("kind").lower()
        value = (match.group("value") or "").strip()
        specs.append(AcceptanceSpec(kind=kind, value=value))
    return TaskSpec(
        goal=_goal_from_text(source),
        acceptance=tuple(specs),
        acceptance_declared=declared,
        stops=stops,
        stop_declared=stop_declared,
        stop_errors=stop_errors,
    )


def resolve_stop_policy(text: str | None = None) -> StopPolicy:
    """解析并校验任务停止条件；旧任务返回全 None，交由运行时默认值接管。"""
    task = parse_task_spec(text)
    budget: int | None = None
    stall_limit: int | None = None
    deadline: datetime | None = None
    errors = list(task.stop_errors)
    for spec in task.stops:
        if not spec.value:
            errors.append(f"停止条件缺少值：{spec.kind}")
            continue
        if spec.kind in {"budget", "stall"}:
            try:
                value = int(spec.value)
            except ValueError:
                errors.append(f"{spec.kind} 必须是整数：{spec.value}")
                continue
            upper = _MAX_TASK_BUDGET if spec.kind == "budget" else _MAX_STALL_LIMIT
            if not 1 <= value <= upper:
                errors.append(f"{spec.kind} 必须在 1..{upper}：{value}")
                continue
            if spec.kind == "budget":
                budget = value
            else:
                stall_limit = value
        elif spec.kind == "deadline":
            try:
                parsed = datetime.fromisoformat(spec.value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"deadline 不是 ISO 8601 时间：{spec.value}")
                continue
            if parsed.tzinfo is None:
                errors.append(f"deadline 必须带时区偏移：{spec.value}")
                continue
            deadline = parsed
    return StopPolicy(
        budget=budget,
        stall_limit=stall_limit,
        deadline=deadline,
        errors=tuple(errors),
    )


def deadline_reached(policy: StopPolicy, now: datetime | None = None) -> bool:
    """判断任务截止时间是否已到；无 deadline 时永不触发。"""
    if policy.deadline is None:
        return False
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return current.astimezone(policy.deadline.tzinfo) >= policy.deadline


def _without_section(text: str, heading: str) -> str:
    """移除指定二级 section，供步骤验收避免把验收描述当执行步骤。"""
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            skipping = True
            continue
        if skipping and re.match(r"^#{1,2}\s+", stripped):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _check_steps(text: str) -> AcceptanceCheck:
    spec = AcceptanceSpec("steps")
    body = _without_section(text, _ACCEPTANCE_HEADING)
    active = [marker for marker in _UNFINISHED_MARKERS + _BLOCKED_MARKERS if marker in body]
    if active:
        return AcceptanceCheck(spec, False, f"仍有未完成或阻塞标记：{', '.join(active)}")
    done, total = count_steps(body)
    if total == 0:
        return AcceptanceCheck(spec, False, "任务正文中没有可验收步骤")
    return AcceptanceCheck(spec, True, f"正文步骤已完成（{done}/{total}）")


def _check_file(value: str) -> AcceptanceCheck:
    spec = AcceptanceSpec("file", value)
    if not value:
        return AcceptanceCheck(spec, False, "file 规则缺少相对路径")
    candidate = resolve_runtime_path(value, PROJECT_ROOT)
    try:
        if not candidate.is_relative_to(PROJECT_ROOT.resolve()):
            candidate.relative_to(TASKS_DIR.parent.resolve())
    except ValueError:
        return AcceptanceCheck(spec, False, "路径越出项目目录（核心项目和个人工作区）")
    if not candidate.exists():
        return AcceptanceCheck(spec, False, f"文件不存在：{value}")
    if not candidate.is_file():
        return AcceptanceCheck(spec, False, f"目标不是文件：{value}")
    return AcceptanceCheck(spec, True, f"文件存在（{candidate.stat().st_size} bytes）")


def _run_acceptance_command(command: str) -> tuple[bool, str]:
    from .verification_receipts import (
        find_valid_receipt,
        is_verification_command,
        record_verification,
    )

    if not command:
        return False, "command 规则缺少命令"
    if any(char in command for char in _SHELL_META_CHARS):
        return False, "验收命令包含 shell 元字符"
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return False, f"命令解析失败：{exc}"
    if not parts or not is_verification_command(command):
        return False, "只允许 pytest、ruff check 或 git diff --check"
    receipt = find_valid_receipt(command, PROJECT_ROOT)
    if receipt is not None:
        state = "通过" if receipt.passed else "失败"
        return (
            receipt.passed,
            f"复用验证回执（{receipt.timestamp}，退出码 {receipt.exit_code}，{state}）；"
            f"{receipt.output_tail[-500:]}",
        )
    try:
        result = subprocess.run(
            parts,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=_ACCEPTANCE_COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"命令执行失败：{exc}"
    output = (result.stdout + "\n" + result.stderr).strip()
    record_verification(command, PROJECT_ROOT, result.returncode, output)
    tail = output[-500:] if output else "无输出"
    if result.returncode != 0:
        return False, f"新执行，退出码 {result.returncode}；{tail}"
    return True, f"新执行，退出码 0；{tail}"


def evaluate_acceptance(text: str | None = None) -> AcceptanceResult:
    """执行 current.md 的结构化验收；旧任务无验收区时兼容放行。"""
    source = read_current() if text is None else text.strip()
    task = parse_task_spec(source)
    if not task.acceptance_declared:
        return AcceptanceResult(configured=False, passed=True, checks=())
    if not task.acceptance:
        missing = AcceptanceCheck(
            AcceptanceSpec("contract"),
            False,
            "已声明 `## 验收`，但没有结构化规则；使用 `steps`、`file:` 或 `command:`",
        )
        return AcceptanceResult(configured=True, passed=False, checks=(missing,))

    checks: list[AcceptanceCheck] = []
    for spec in task.acceptance:
        if spec.kind == "steps":
            checks.append(_check_steps(source))
        elif spec.kind == "file":
            checks.append(_check_file(spec.value))
        elif spec.kind == "command":
            passed, evidence = _run_acceptance_command(spec.value)
            checks.append(AcceptanceCheck(spec, passed, evidence))
    return AcceptanceResult(
        configured=True,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )


def _record_acceptance_result(result: AcceptanceResult) -> None:
    """把成功验收证据写回 current.md，随后随任务一起归档。"""
    text = read_current()
    if not text or not result.configured or not result.passed:
        return
    text = _without_section(text, _ACCEPTANCE_RESULT_HEADING).rstrip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    receipt = f"{_ACCEPTANCE_RESULT_HEADING}\n\n验证时间：{timestamp}\n\n{result.render()}"
    CURRENT_TASK_FILE.write_text(f"{text}\n\n{receipt}\n", encoding="utf-8")


def _task_links(text: str) -> tuple[str, ...]:
    """Derive cross-subsystem references without taking ownership of their state."""
    gap_ids = re.findall(r"\bgap[\s:#-]+([0-9a-f]{12})\b", text, flags=re.IGNORECASE)
    return tuple(f"gap:{gap_id.lower()}" for gap_id in dict.fromkeys(gap_ids))


def _accepted_artifacts(result: AcceptanceResult) -> tuple[Path, ...]:
    return tuple(
        PROJECT_ROOT / check.spec.value
        for check in result.checks
        if check.passed and check.spec.kind == "file" and check.spec.value
    )


def archive_current_if_accepted(slug: str = "") -> str:
    """自动归档入口：有验收合同则通过后归档，旧任务沿用原行为。"""
    task_text = read_current()
    result = evaluate_acceptance()
    task_key = ""
    with contextlib.suppress(Exception):
        from .asset_usage import current_task_key, record_task_outcome

        task_key = current_task_key(task_text)
        record_task_outcome(
            "passed" if result.passed else "failed",
            result.render(),
            task_key=task_key,
        )
    if not result.passed:
        with contextlib.suppress(Exception):
            from .work_receipts import record_work_receipt

            record_work_receipt(
                "task",
                task_key,
                "failed",
                goal=parse_task_spec(task_text).goal,
                evidence=result.render(),
                capture_workspace=True,
                links=_task_links(task_text),
            )
        return f"❌ 任务未归档。\n{result.render()}"
    _record_acceptance_result(result)
    archive_message, destination = _archive_current(slug)
    with contextlib.suppress(Exception):
        from .work_receipts import record_work_receipt

        artifacts = (*_accepted_artifacts(result), *((destination,) if destination else ()))
        record_work_receipt(
            "task",
            task_key,
            "completed",
            goal=parse_task_spec(task_text).goal,
            evidence=result.render(),
            artifact_paths=artifacts,
            capture_workspace=True,
            links=_task_links(task_text),
        )
    if result.configured:
        return f"{result.render()}\n{archive_message}"
    return archive_message


def has_unfinished() -> bool:
    """current.md 存在且含活跃未完成步骤（[ ] / [o]）。

    [r]（需重试）和 [!]（阻塞）不算"活跃未完成"——
    有这些标记时 agent 不需要自动续做，但也不算全完成。
    """
    text = read_current()
    return bool(text) and any(marker in text for marker in _UNFINISHED_MARKERS)


# is_all_done 已删除（2026-06-24）：唯一调用方 make_task_context_message 删除后零生产调用；
# 任务完成→归档由 task_loop 自有逻辑处理，不经此谓词。


def resume_reminder() -> str | None:
    """会话首轮若有未完成长任务，返回一段恢复提醒（含目标锚点 + 任务清单），否则 None。

    治"可恢复"：current.md 持久化、会话日志每轮 autosave——状态从不丢，但跨会话/重启后
    agent 对"有个没做完的任务"没有注意力。main 在会话首轮 append-only 注入这一条（永久 log、
    非挂尾），让重开就能从断点接着干。一次性注入（不每轮 bloat），见 main.run_agent_turn。
    """
    if not has_unfinished():
        return None
    text = read_current()
    head = ("⏸ 你有一个未完成的长任务（tasks/current.md），从未完成步骤（[ ]/[o]）的断点继续，"
            "不要从头重来；在步骤旁记录细进度（如 47/250），完成后清空归档。")
    anchor = _extract_anchor(text)
    if anchor:
        head += f"\n🎯 目标锚点：{anchor}（每完成一步对照检查方向）"
    return head + "\n\n" + text


# make_task_context_message（挂尾 per-turn 任务上下文）已删除（2026-06-24）：随挂尾机制废止后
# 它生产侧零调用、是 dormant 孤儿。其活的部分已各归各位——未完成任务提醒→resume_reminder
# (main.run_agent_turn)、自动归档→task_loop、门通行证审计→main.run_agent_turn(append-only)、
# 方向发现→/pulse 按需。经验检索(experience.py)随之删除（Jaccard 粗匹配，留待语义版重做）。


def create_task(content: str) -> str:
    """写入 current.md。必须有 # 目标锚点，拒绝写入否则漂移无绳。

    自动追加归档步骤（方案 A）。
    """
    final = content.strip()
    if _ANCHOR_HEADING not in final:
        return (
            "拒绝：缺少 # 目标锚点。\n"
            "请在任务内容中加入一行 '# 目标锚点' 和一句描述——"
            "说清这个任务到底要解决什么问题。\n"
            "例如：\n"
            "  # 目标锚点\n  让 current.md 从'旗'变成'绳'，每步自动对照方向。"
        )
    if "- [ ] 归档" not in final:
        final += "\n- [ ] 归档 current.md → tasks/archive/"
    CURRENT_TASK_FILE.write_text(final + "\n", encoding="utf-8")
    return "已写入 current.md（含目标锚点 + 自动归档步骤）。"


def _derive_slug(text: str) -> str:
    """从首个 Markdown 标题派生归档用 slug；取不到用 fallback。"""
    for line in text.splitlines():
        stripped = line.lstrip("# ").strip()
        if line.startswith("#") and stripped:
            slug = re.sub(r"[^\w一-鿿]+", "-", stripped).strip("-")
            return slug or _SLUG_FALLBACK
    return _SLUG_FALLBACK


def _archive_current(slug: str = "") -> tuple[str, Path | None]:
    text = read_current()
    if not text:
        return "current.md 为空，无可归档。", None
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    safe_slug = re.sub(r"[^\w一-鿿-]+", "-", slug).strip("-") or _derive_slug(text)
    dest = ARCHIVE_DIR / f"{date}-{safe_slug}.md"
    dest.write_text(text + "\n", encoding="utf-8")
    CURRENT_TASK_FILE.write_text("", encoding="utf-8")
    return f"已归档 → tasks/archive/{dest.name}，current.md 已清空。", dest


def archive_current(slug: str = "") -> str:
    """Low-level compatibility archive; verified completion uses archive_current_if_accepted."""
    message, _destination = _archive_current(slug)
    return message


def clear_current() -> str:
    """清空 current.md（不归档，用于放弃任务）。"""
    task_text = read_current()
    if not task_text:
        return "current.md 已是空的。"
    task_key = ""
    with contextlib.suppress(Exception):
        from .asset_usage import current_task_key, record_task_outcome

        task_key = current_task_key(task_text)
        record_task_outcome("abandoned", "current.md 被显式清空且未归档", task_key=task_key)
    with contextlib.suppress(Exception):
        from .work_receipts import record_work_receipt

        record_work_receipt(
            "task",
            task_key,
            "abandoned",
            goal=parse_task_spec(task_text).goal,
            evidence="current.md 被显式清空且未归档",
            capture_workspace=True,
            links=_task_links(task_text),
        )
    CURRENT_TASK_FILE.write_text("", encoding="utf-8")
    return "current.md 已清空（未归档）。"


def update_plan(content: str) -> str:
    """覆盖更新 current.md（agent 动态重规划用）。必须有 # 目标锚点。

    与 create_task 不同：不自动追加归档步骤（已在计划中的步骤不动）。
    让 agent 可以增删改步骤、重排序、更新描述。
    """
    final = content.strip()
    if _ANCHOR_HEADING not in final:
        return (
            "拒绝：缺少 # 目标锚点。\n"
            "更新计划必须保留目标锚点——它是方向之绳。\n"
            "请加入 '# 目标锚点' 一行和一句描述。"
        )
    CURRENT_TASK_FILE.write_text(final + "\n", encoding="utf-8")
    done, total = count_steps(final)
    return f"✅ 计划已更新（{done}/{total} 步骤完成）:\n{final}"


def count_steps(text: str) -> tuple[int, int]:
    """统计 current.md 内容中的已完成/总步骤数。返回 (done, total)。"""
    done = 0
    total = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            done += 1
            total += 1
        elif stripped.startswith(("- [ ]", "- [o]", "- [r]", "- [!]")):
            total += 1
    return done, total


def get_task_short_status() -> str:
    """返回简练的任务状态行，给状态栏用。如 '📋 任务名 (2/5)'。"""
    text = read_current()
    if not text:
        return ""
    done, total = count_steps(text)
    # 取标题（首个 # 或 ## 行）
    title = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("# 目标锚点"):
            title = s[2:].strip()
            break
        if s.startswith("## ") and title == "":
            title = s[3:].strip()
    if not title:
        title = "任务"
    if total == 0:
        return f"📋 {title}"
    return f"📋 {title} ({done}/{total})"


def read_current_active_step() -> str | None:
    """任务切片：解析 current.md，只返回当前第一个活跃的步骤和其子描述。

    避免把做完的 [x] 和整个长列表全喂给模型。
    返回格式：步骤行 + 紧跟的子描述。无未完成步骤时返回 None。
    """
    text = read_current()
    if not text:
        return None

    active_lines: list[str] = []
    found_active = False

    for line in text.splitlines():
        stripped = line.strip()
        # 遇到第一个未完成的步骤，开始记录
        if stripped.startswith(("- [ ]", "- [o]", "- [O]")):
            found_active = True
            active_lines.append(line)
        # 如果已经找到了活跃步骤，且遇到了下一个步骤标记 → 结束
        elif found_active and stripped.startswith("- ["):
            break
        # 将紧跟的子描述也带上
        elif found_active:
            active_lines.append(line)

    result = "\n".join(active_lines).strip()
    return result if result else None
