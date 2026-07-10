"""Termination Gate — 证据驱动的 agent loop 终止验证。

Loop Engineering 的核心洞察：「verifier 是瓶颈，不是模型」。
在 agent 自然终止点（LLM 返回无 tool_calls 时）插入证据驱动的检查。
验证不通过 → loop 不终止，注入具体反馈让 agent 修正。

设计原则：
  - 证据驱动：检查文件系统/任务状态，不靠 agent 自评
  - 快速：纯本地检查，不走 LLM，<100ms
  - 具体：失败时告诉 agent 具体缺什么
  - 分层：fail=拦截继续 / warn=提醒后放行 / pass=直接放行
  - 不崩溃：任何检查异常静默吞掉，绝不让 verifier 本身成为故障点
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache_context import CacheContext


@dataclass
class GateResult:
    """单条检查结果。"""

    check_name: str
    passed: bool
    severity: str  # "pass" | "warn" | "fail"
    message: str = ""


# ── Git 变更检测 ──

def _get_git_changes() -> tuple[set[str], set[str], bool]:
    """返回 (modified_files, untracked_files, is_git_repo)。

    modified: 已追踪文件中有改动的（git diff --name-only）
    untracked: 未追踪的新文件（git ls-files --others --exclude-standard）
    is_git_repo: 是否在 git 仓库中，False 时前两者为空——调用方应 fallback 到简单存在性检查
    """
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, check=True, timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return set(), set(), False

    modified: set[str] = set()
    untracked: set[str] = set()

    with contextlib.suppress(Exception):
        r = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            modified = {line.strip() for line in r.stdout.splitlines() if line.strip()}

    with contextlib.suppress(Exception):
        r = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            untracked = {line.strip() for line in r.stdout.splitlines() if line.strip()}

    return modified, untracked, True


# ── 文件路径提取 ──

_FILE_PATH_RE = re.compile(
    r'(?:[\w.-]+/)*[\w.-]+\.(?:py|md|txt|json|yaml|yml|toml|cfg|ini|js|ts|html|css|sh|bat|ps1)'
)

# 匹配「做了某文件」的汉语表述
_CLAIM_RE = re.compile(
    r'(创建|修改|编辑|更新|写入|写了|新建|生成|改了|完成)'
    r'(?:了?的?)'
    r'(?:文件|代码|模块)?\s*'
    r'(?:`([^`]+)`|([^\s，。,;\n]{3,80}))',
)


def _extract_claimed_files(text: str) -> dict[str, tuple[str, str]]:
    """从 agent 回复中提取声称创建/修改的文件路径。

    返回 {路径: (动作动词, 来源)}，来源 ∈ {"verb", "backtick"}：
      - "verb":     动作动词紧邻路径（"我创建了 `x.py`"）——**强信号**，缺证据判 fail。
      - "backtick": 仅靠反引号文件名模式命中、无显式动作动词——**弱信号**。评审/讨论
                    轮里引用一个已有文件（"看下 `x.py`"）也会命中，缺证据只降 warn、
                    不拦 loop；否则纯讲解轮会被硬 fail 卡住（实测会误伤）。
    """
    claimed: dict[str, tuple[str, str]] = {}
    for m in _CLAIM_RE.finditer(text):
        action = m.group(1)
        for group_idx in (2, 3):
            maybe_path = m.group(group_idx)
            if maybe_path and _looks_like_filepath(maybe_path):
                claimed[maybe_path] = (action, "verb")
    # 额外：反引号内的任何文件名模式（无显式动词——弱信号，仅提醒不拦截）
    for m in re.finditer(r'`([a-zA-Z0-9_/.-]+\.[a-z]{1,6})`', text):
        path = m.group(1)
        if _looks_like_filepath(path) and path not in claimed:
            claimed[path] = ("修改", "backtick")
    return claimed


def _looks_like_filepath(s: str) -> bool:
    """判断字符串是否像文件路径（有扩展名、不是自然语句）。"""
    # 纯标点/过短不是
    if len(s) < 3:
        return False
    # 有常见扩展名
    if not re.search(r'\.(py|md|txt|json|yaml|yml|toml|cfg|ini|js|ts|html|css|sh|bat|ps1)\b', s):
        return False
    # 不含中文字符（过滤「更新了代码」这类）
    return not re.search(r'[\u4e00-\u9fff]', s)


# ── 检查函数 ──

def check_claim_evidence(assistant_content: str, _ctx) -> GateResult:
    """检查 1: 声称-证据一致性。

    Agent 回复中声称创建/修改了文件 → 验证（1）文件存在 + （2）有实际改动证据。

    在 git 仓库中会交叉验证 git diff 和 untracked 文件；
    非 git 仓库仅检查文件存在性。
    """
    claimed = _extract_claimed_files(assistant_content)
    if not claimed:
        return GateResult("声称-证据一致性", True, "pass")

    cwd = Path.cwd()
    modified, untracked, is_git_repo = _get_git_changes()

    # 按来源分强/弱：动词绑定=强信号缺证据判 fail；仅反引号=弱信号只 warn。
    strong_missing: list[str] = []
    strong_no_evidence: list[str] = []
    weak: list[str] = []          # 弱信号的缺证据（不存在/无改动都归这，只提醒）

    for path_str, (_action, source) in claimed.items():
        p = cwd / path_str
        exists = p.exists()
        has_evidence = not (is_git_repo and (modified or untracked)
                            and path_str not in modified and path_str not in untracked)
        if exists and has_evidence:
            continue  # 存在且有改动证据（或无 git 数据可交叉验证）→ 无问题
        if source == "verb":
            (strong_missing if not exists else strong_no_evidence).append(path_str)
        else:
            weak.append(path_str)

    def _fmt(paths: list[str]) -> str:
        head = "、".join(paths[:5])
        return head + (f" 等 {len(paths)} 个" if len(paths) > 5 else "")

    # 强信号缺证据 → fail（拦 loop，逼补齐或改正）
    fail_msgs: list[str] = []
    if strong_missing:
        fail_msgs.append(
            f"你的回复中声称创建/修改了以下文件但它们不存在: {_fmt(strong_missing)}。"
            f"请实际创建/修改这些文件，或修正回复中不准确的描述。"
        )
    if strong_no_evidence:
        fail_msgs.append(
            f"你的回复中声称修改了以下文件但 git diff 显示没有实际改动: {_fmt(strong_no_evidence)}。"
            f"请实际修改这些文件，或修正回复中不准确的描述。"
        )
    if fail_msgs:
        return GateResult("声称-证据一致性", False, "fail", " ".join(fail_msgs))

    # 仅弱信号（反引号引用）缺证据 → warn，追加提醒后正常终止，不拦 loop
    if weak:
        return GateResult(
            "声称-证据一致性", False, "warn",
            f"💡 你的回复里以反引号提到了这些文件，但未检测到对应改动 / 文件不存在: "
            f"{_fmt(weak)}。若只是引用讨论可忽略；若确实是完成声明，请补齐证据。",
        )
    return GateResult("声称-证据一致性", True, "pass")


def check_task_consistency(_assistant_content: str, ctx) -> GateResult:
    """检查 2: 任务一致性。

    current.md 有未完成步骤但 agent 没调 task_done/need_user → 提醒。
    """
    try:
        from .tasks import _UNFINISHED_MARKERS as UNFINISHED_MARKERS
        from .tasks import read_current

        current = read_current()
        if not current.strip():
            return GateResult("任务一致性", True, "pass")

        has_unfinished = any(m in current for m in UNFINISHED_MARKERS)
        if not has_unfinished:
            return GateResult("任务一致性", True, "pass")

        ctrl = getattr(ctx, "control_signal", None)
        if ctrl in ("task_done", "need_user"):
            return GateResult("任务一致性", True, "pass")

        return GateResult(
            "任务一致性", False, "warn",
            "⚠️ current.md 中还有未完成步骤，但你本轮未调用 task_done 或 need_user。"
            "如果任务确实没做完请继续；如果只是忘了更新步骤标记，请更新 current.md。",
        )
    except Exception:
        return GateResult("任务一致性", True, "pass")


def _this_turn_log(ctx) -> list[dict]:
    """截取「这一轮」的 log 片段：最后一条 user 消息之后的部分。

    没有 user 消息（异常场景）时回退全量——宁可多报，不误判整段历史为空。
    """
    log = ctx.log
    for i in range(len(log) - 1, -1, -1):
        if log[i].get("role") == "user":
            return log[i:]
    return log


def check_modified_tests(_assistant_content: str, ctx) -> GateResult:
    """检查 3: 修改后测试提醒。

    本轮修改了 .py 文件 → 提醒跑测试（只提醒，不拦截）。

    两处曾经的真实 bug（均已修）：
    1. 扫描过全量 ctx.log 而非"本轮"——旧代码没有 user 消息边界，只要会话里任何
       一轮碰过 .py 文件，之后每一轮（哪怕没碰 Python）都会重新报出同一批旧文件名，
       越滚越多、每轮内容都不同 → 当成新 system 消息 append，log 里堆出大量相似
       但不完全相同的提醒。
    2. 同一批文件名在本轮内被反复触发（gate fail→retry 场景）时会整条重复 append，
       没有去重——已加 last-warning 内容比对短路。
    """
    py_modified: set[str] = set()
    for msg in _this_turn_log(ctx):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name in ("write_file", "replace_in_file"):
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                    path = args.get("path", "")
                    if path.endswith(".py"):
                        py_modified.add(path)
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

    if not py_modified:
        return GateResult("修改后测试提醒", True, "pass")

    files = sorted(py_modified)[:5]
    files_list = "、".join(files)
    suffix = f" 等 {len(py_modified)} 个文件" if len(py_modified) > 5 else ""
    message = (
        f"💡 本轮修改了 Python 文件: {files_list}{suffix}。"
        f"建议运行 `python -m pytest tests/` 验证改动没有破坏现有功能。"
    )

    # 去重：本轮内已经原样提醒过（gate fail→retry 场景）就不再重复 append
    already_warned = any(
        m.get("role") == "system" and m.get("content") == message
        for m in _this_turn_log(ctx)
    )
    if already_warned:
        return GateResult("修改后测试提醒", True, "pass")

    return GateResult("修改后测试提醒", False, "warn", message)


# ── Gate 执行器 ──

class TerminationGate:
    """注册检查项 → 运行 → 返回聚合结果。"""

    def __init__(self) -> None:
        self._checks: list = []

    def register(self, check_fn) -> None:
        """注册检查函数。签名: (assistant_content: str, ctx) -> GateResult"""
        self._checks.append(check_fn)

    def run(self, assistant_content: str, ctx: CacheContext) -> list[GateResult]:
        """运行所有已注册的检查，返回结果列表。异常自动吞掉。"""
        results: list[GateResult] = []
        for check in self._checks:
            with contextlib.suppress(Exception):
                results.append(check(assistant_content, ctx))
        return results


# 默认 gate（Phase 1 三项检查）
_default_gate = TerminationGate()
_default_gate.register(check_claim_evidence)
_default_gate.register(check_task_consistency)
_default_gate.register(check_modified_tests)


def run_termination_gate(assistant_content: str, ctx: CacheContext) -> str:
    """运行终止门。返回动作字符串:

    "pass"        → 直接终止
    "warn:msg"    → 追加提醒后终止
    "fail:msg"    → 注入反馈到 log，loop 继续
    """
    results = _default_gate.run(assistant_content, ctx)

    # 找最严重的
    worst_severity = "pass"
    msgs: list[str] = []
    for r in results:
        if r.severity == "fail":
            worst_severity = "fail"
            msgs.append(r.message)
        elif r.severity == "warn" and worst_severity != "fail":
            worst_severity = "warn"
            msgs.append(r.message)

    if worst_severity == "pass":
        return "pass"
    nl = "\n"
    return f"{worst_severity}:{nl.join(msgs)}"
