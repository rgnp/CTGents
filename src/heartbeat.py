"""心跳：无人期自主推进探索前沿（tasks/frontier.md）。

设计（对话 2026-07-17，用户拍板）——干活多、说话少：
  - 活从哪来：只消费 frontier.md 里用户种下的方向；worker 发现的新线索只能写进
    「候选方向」区等用户转正，不许自己新开活跃项（防机制生成器式的自我发明）。
  - 干多少：每次醒来领第一个活跃项做深（质量密度优先于数量），请求预算
    HEARTBEAT.worker_max_requests，收尾把断点写回 frontier——断点续跑的唯一真相源
    是 frontier 文件，不是对话记忆。
  - 防垃圾：worker 前缀硬加载领域 psyche（没加载=开放判断塌，实测 5a96363/psyche
    档案），knowledge/ 产出过 delegate_gate 机械出处闸，没过打回重试一次、仍不过
    如实记进摘要（fail-visible，不静默放行也不静默丢弃）。
  - 防话痨：默认产出是沉默——工作落文件，用户只在回主会话时收到一条合并摘要
    （digest 注入见 main.run_agent_turn，append-only、缓存安全）。
  - 防空转：无活跃项静默退出（零 LLM、零 token）；连续 stall_limit 次没推进
    frontier 自暂停，frontier 被人改动（mtime 更新）后自动恢复；每日次数上限兜底。
  - 隔离：全新 CacheContext + 工具白名单（无 run_command/git/删除——无人期不动
    系统状态）+ session_id=""（不落存档）+ track_stats=False，同 delegate 隔离清单。

入口：
  python -m src.heartbeat            # 单次（给 schtasks/cron 调）
  python -m src.heartbeat --loop 1800  # 常驻循环，每 30 分钟一跳
  /heartbeat [run]                   # REPL 内看状态 / 手动触发一跳（测试用，阻塞）

模块顶层保持轻 import（main 每轮调 consume_digest）；llm/tools 惰性到 _work 里。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .params import HEARTBEAT
from .tasks import _BLOCKED_MARKERS, _UNFINISHED_MARKERS, TASKS_DIR

FRONTIER_FILE = TASKS_DIR / "frontier.md"
HEARTBEAT_DIR = TASKS_DIR / "heartbeat"

# 锁超过此秒数视为陈旧（上次进程崩了没清），可抢占
_LOCK_STALE_SECONDS = 2 * 3600
# 单条摘要条目的字符上限（worker 小结失控长时截断，摘要必须是摘要）
_DIGEST_ENTRY_MAX_CHARS = 1600

_WORKER_SYSTEM = """你是 CTGents 的心跳 worker：无人值守、干净上下文、跑完即散。
你的职责是趁用户不在时，把探索前沿（科研调研）向前推进扎实的一小步。
质量密度优先于数量：宁可把一篇论文摸深（方法/证据/边界/与已有卡片的矛盾），
不要把十篇过成流水账。你无法向任何人提问，拿不准就降级标注、停牌，不硬编。"""


@dataclass(frozen=True)
class HeartbeatWorkResult:
    """Worker result retained until run_once can judge actual frontier progress."""

    message: str
    evidence: str
    artifact_paths: tuple[str, ...]
    gate_passed: bool


# ── 路径（函数取值，测试 monkeypatch HEARTBEAT_DIR/FRONTIER_FILE 即整体重定向）──

def _state_path() -> Path:
    return HEARTBEAT_DIR / "state.json"


def _digest_pending_path() -> Path:
    return HEARTBEAT_DIR / "digest-pending.md"


def _digest_archive_path() -> Path:
    return HEARTBEAT_DIR / "digest-archive.md"


def _lock_path() -> Path:
    return HEARTBEAT_DIR / "lock"


# ── frontier 读取与判活 ──

def read_frontier() -> str:
    if not FRONTIER_FILE.exists():
        return ""
    return FRONTIER_FILE.read_text(encoding="utf-8")


def has_active_work(text: str | None = None) -> bool:
    """Frontier 存在且含活跃项（[ ]/[o]）。心跳的机械预检：不满足即静默退出，零 LLM。"""
    if text is None:
        text = read_frontier()
    return bool(text.strip()) and any(m in text for m in _UNFINISHED_MARKERS)


# ── 状态 ──

def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _state_path())


def _roll_date(state: dict) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["date"] = today
        state["runs_today"] = 0


def _is_paused(state: dict) -> bool:
    """自暂停中？frontier 在暂停之后被人改动（mtime 更新）→ 自动恢复。"""
    paused_at = state.get("paused_at") or 0
    if not paused_at:
        return False
    try:
        if FRONTIER_FILE.stat().st_mtime > paused_at:
            state["paused_at"] = 0
            state["stalls"] = 0
            return False
    except OSError:
        pass
    return True


# ── 锁（防两次心跳重叠：上一跳没跑完，下一跳直接让路）──

def _acquire_lock() -> bool:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    lock = _lock_path()
    if lock.exists():
        try:
            if time.time() - lock.stat().st_mtime < _LOCK_STALE_SECONDS:
                return False
        except OSError:
            return False
    try:
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return False


def _release_lock() -> None:
    import contextlib
    with contextlib.suppress(OSError):
        _lock_path().unlink(missing_ok=True)


# ── 摘要（pending 攒着，用户回主会话时一次性消费）──

def _append_digest(entry: str) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"## {stamp}\n{entry.strip()}\n\n"
    with open(_digest_pending_path(), "a", encoding="utf-8") as f:
        f.write(block)


def pending_digest() -> str | None:
    p = _digest_pending_path()
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text or None


def consume_digest() -> str | None:
    """取走待送摘要（归档后清空 pending）。主会话每轮开头调，无摘要时零成本。"""
    text = pending_digest()
    if not text:
        return None
    with open(_digest_archive_path(), "a", encoding="utf-8") as f:
        f.write(text + "\n\n")
    with contextlib.suppress(Exception):
        from .work_receipts import (
            derive_work_id,
            record_work_receipt,
            undelivered_work_links,
        )

        record_work_receipt(
            "heartbeat",
            derive_work_id("heartbeat", text),
            "delivered",
            goal="Heartbeat 合并交还",
            evidence=text,
            links=undelivered_work_links(),
        )
    _digest_pending_path().unlink(missing_ok=True)
    return text


# ── worker ──

def _worker_tools() -> list[dict]:
    from .tools import get_tools_subset
    names = frozenset(
        n.strip() for n in HEARTBEAT.worker_tools.split(",")
        if n.strip() and n.strip() != "delegate"
    )
    return get_tools_subset(names)


def _work_order(frontier_text: str, budget: int) -> str:
    return (
        "[心跳·自主推进] 现在是无人值守的心跳时段。"
        "下面是探索前沿文件 tasks/frontier.md 的当前内容：\n\n"
        "```markdown\n" + frontier_text.strip() + "\n```\n\n"
        "规则：\n"
        "1. 只领一项：选「方向」区第一个活跃项（[ ] 或 [o]），把它向前推进扎实的一步，不贪多。\n"
        "2. 产出落盘：论文卡片/笔记用 write_file 写 knowledge/ 下文件。每个事实断言带来源 URL；"
        "[已核] 标注所在行必须带该 URL 且你真用 read_page 读过原文——产出会过机械出处闸，"
        "闸核工具调用记录、不看措辞。只看了摘要就标 [未核·仅摘要]。\n"
        "3. 收尾必须用 write_file 更新 tasks/frontier.md：改步骤标记、在项旁记断点"
        "（做到哪、下一步从哪接）。发现值得新开的方向只能写进「候选方向」区等用户转正，"
        "不得自己往「方向」区加活跃项。\n"
        "4. 该项无法继续（缺信息/外部故障/需要用户拍板）→ 把它标成 [r] 并注明原因，停牌不硬试。\n"
        "5. 项要求跑成套流程（如论文入库）时：psyche_catalog 查目录 → load_psyche 加载 "
        "owner psyche → activate_skill 加载流程 skill（如 paper-pipeline，axes 里 "
        "stage=resume 断点续跑），然后严格按 skill 步骤执行。\n"
        f"6. 请求预算 {budget} 次，到额前留 2-3 步做收尾落盘。"
        "最后用一条纯文本回复给出 ≤6 行小结（干了什么/产出文件/下一步断点），"
        "这条小结会作为摘要呈给用户。"
    )


def _gate_written(report: str, written: list[str], worker_log: list[dict],
                  evidence: str) -> list[str]:
    """对本次写入 knowledge/ 的文件逐个跑机械出处闸，问题带文件名前缀。"""
    from .delegate_gate import gate_check
    root = TASKS_DIR.parent
    problems: list[str] = []
    for rel in dict.fromkeys(written):  # 去重保序
        norm = rel.replace("\\", "/").lstrip("./")
        if not norm.startswith("knowledge/"):
            continue
        problems += [f"[{norm}] {p}" for p in gate_check(report, root / norm, worker_log, evidence)]
    return problems


def _gate_feedback(problems: list[str]) -> str:
    """闸反馈。开头必须与 delegate_gate._FEEDBACK_PREFIX 一致——闸取证时按此前缀
    把反馈自身从 haystack 剔除，防止反馈里点名的编造 URL 被闸自己洗白。
    """
    listing = "\n".join(f"  - {p}" for p in problems)
    return (
        f"⛔ 出处闸未通过（机械核查，不看措辞）：\n{listing}\n\n"
        "标注反映核实程度，不是自信程度。正道：\n"
        "1. 未 grounding 的 URL：对它调 read_page 读到原文后重报，或删掉该引用；\n"
        "2. 没真读过的 [已核]：改标 [未核·仅摘要]，或补 read_page 后保留；\n"
        "3. 修正对应 knowledge/ 文件后重新给出最终小结。"
    )


def _work(frontier_before: str, log_fn) -> HeartbeatWorkResult:
    """Run one isolated worker and retain evidence for the shared receipt."""
    from . import llm as _llm
    from . import tracker
    from .cache_context import CacheContext
    from .psyche_bridge import inject_psyche

    ctx = CacheContext(prefix_msgs=[{"role": "system", "content": _WORKER_SYSTEM}])
    # 事件化注入（非拼 core 原文进前缀）：走 _psyche_event，worker 因此能过
    # activate_skill 的 "owner psyche 必须 active" 检查，paper-pipeline 等 skill 才可用。
    # fail-closed：psyche 加载不上就不干活——没有领域判断力的无人产出是垃圾源头。
    note = inject_psyche(ctx, HEARTBEAT.psyche, scope="session", source="user",
                         reason="heartbeat 无人期硬加载")
    if note.startswith("❌"):
        raise RuntimeError(f"psyche 硬加载失败，本跳中止：{note}")

    evidence: list[str] = []
    written: list[str] = []

    def _on_tool(name: str, args: dict) -> None:
        args_json = json.dumps(args, ensure_ascii=False)
        evidence.append(f"{name} {args_json}")
        if name == "write_file" and isinstance(args.get("path"), str):
            written.append(args["path"])
        log_fn(f"  ↳ [heartbeat] {name}({args_json[:80]})")

    def _run(prompt: str, budget: int) -> str:
        return _llm.run_conversation(
            ctx, prompt,
            on_token=lambda _t: None,
            on_tool=_on_tool,
            session_id="",           # 不落 sessions/ 存档
            tools=_worker_tools(),
            track_stats=False,       # 不碰主会话缓存统计
            max_requests=budget,
        )

    main_sid = tracker.current_session()
    try:
        report = _run(
            _work_order(frontier_before, HEARTBEAT.worker_max_requests),
            HEARTBEAT.worker_max_requests,
        )
        problems = _gate_written(report, written, ctx.log, "\n".join(evidence))
        if problems:
            report = _run(_gate_feedback(problems), HEARTBEAT.retry_max_requests)
            problems = _gate_written(report, written, ctx.log, "\n".join(evidence))
    finally:
        tracker.set_session(main_sid)  # run_conversation 无条件覆盖会话指针，恢复主会话归属

    summary = (report or "").strip() or "（worker 没有给出小结）"
    if len(summary) > _DIGEST_ENTRY_MAX_CHARS:
        summary = summary[:_DIGEST_ENTRY_MAX_CHARS] + "…"

    lines = [summary]
    knowledge_written = [w for w in dict.fromkeys(written)
                         if w.replace("\\", "/").lstrip("./").startswith("knowledge/")]
    if knowledge_written:
        lines.append("📄 本次写入: " + ", ".join(knowledge_written))
    if getattr(ctx, "control_signal", None) == "need_user":
        lines.append(f"⏸ worker 请求拍板：{getattr(ctx, 'control_payload', '') or '（未说明）'}")
    gate_line = ("✅ 出处闸: 通过" if not problems
                 else "⛔ 出处闸: 未通过\n" + "\n".join(f"  - {p}" for p in problems))
    lines.append(gate_line)
    _append_digest("\n".join(lines))

    n_calls = sum(1 for m in ctx.log if m.get("role") == "assistant")
    message = (
        f"完成一跳：{n_calls} 次 LLM 响应，"
        f"写入 {len(knowledge_written)} 个 knowledge 文件，{gate_line.splitlines()[0]}"
    )
    return HeartbeatWorkResult(
        message=message,
        evidence="\n".join(lines),
        artifact_paths=tuple(knowledge_written),
        gate_passed=not problems,
    )


def _frontier_work_identity(text: str) -> tuple[str, str]:
    """Use the anchor and first active item as the Heartbeat-owned stable identity."""
    active = next(
        (
            line.strip()
            for line in text.splitlines()
            if any(marker in line for marker in _UNFINISHED_MARKERS)
        ),
        "",
    )
    goal = active
    with contextlib.suppress(Exception):
        from .tasks import _extract_anchor

        anchor = _extract_anchor(text)
        if anchor:
            goal = f"{anchor} — {active}"
    from .work_receipts import derive_work_id

    return derive_work_id("heartbeat", goal), goal


def _record_run_receipt(
    frontier_before: str,
    stage: str,
    evidence: str,
    artifacts: tuple[str, ...] = (),
    run_id: str = "",
) -> None:
    with contextlib.suppress(Exception):
        from .work_receipts import record_work_receipt

        work_id, goal = _frontier_work_identity(frontier_before)
        record_work_receipt(
            "heartbeat",
            work_id,
            stage,
            goal=goal,
            evidence=evidence,
            artifact_paths=artifacts,
            idempotency_key=run_id,
        )


# ── 主流程 ──

def run_once(force: bool = False, log_fn=print) -> str:
    """一次完整心跳。返回一行结果（静默路径也返回原因，供 CLI/日志/状态查看）。

    force=True 跳过每日上限与自暂停（手动 /heartbeat run 测试用），但不跳过
    「无活跃项」预检——没活干强行叫醒 LLM 没有意义。
    """
    if not HEARTBEAT.enabled:
        return "跳过：心跳已禁用（CTG_HEARTBEAT_ENABLED=0）"

    frontier_before = read_frontier()
    if not has_active_work(frontier_before):
        return "静默：frontier 无活跃项（[ ]/[o]），零 LLM 退出"

    state = _load_state()
    _roll_date(state)
    if not force:
        if _is_paused(state):
            return "静默：已自暂停（连续无推进），改动 tasks/frontier.md 后自动恢复"
        if state.get("runs_today", 0) >= HEARTBEAT.max_runs_per_day:
            return f"静默：今日已达上限 {HEARTBEAT.max_runs_per_day} 次"
    if not _acquire_lock():
        return "跳过：另一次心跳还在跑（锁未释放）"

    run_id = f"heartbeat-run:{uuid.uuid4().hex}"
    try:
        try:
            work_result = _work(frontier_before, log_fn)
            outcome = work_result.message
        except Exception as e:  # noqa: BLE001  无人值守：任何异常都要落状态+摘要，不许静默消失
            outcome = f"失败：{type(e).__name__}: {e}"
            _append_digest(f"⚠️ 心跳异常中止：{outcome}")
            work_result = None

        # 推进检测：frontier 没变 = 空转一跳。连续 stall_limit 次 → 自暂停等用户修剪。
        progressed = read_frontier() != frontier_before
        if not progressed:
            state["stalls"] = state.get("stalls", 0) + 1
            if state["stalls"] >= HEARTBEAT.stall_limit:
                state["paused_at"] = time.time()
                _append_digest(
                    f"⏸ 心跳已自暂停：连续 {state['stalls']} 跳没推进 frontier"
                    "（可能卡住或方向需要你修剪）。改动 tasks/frontier.md 后自动恢复。"
                )
                outcome += "；连续无推进，已自暂停"
        else:
            state["stalls"] = 0

        if work_result is None:
            _record_run_receipt(frontier_before, "failed", outcome, run_id=run_id)
        else:
            passed = work_result.gate_passed and progressed
            receipt_evidence = work_result.evidence
            if not progressed:
                receipt_evidence += "\n未通过推进验收：frontier 未变化。"
            _record_run_receipt(
                frontier_before,
                "completed" if passed else "failed",
                receipt_evidence,
                work_result.artifact_paths,
                run_id,
            )

        state["runs_today"] = state.get("runs_today", 0) + 1
        state["last_run"] = time.time()
        state["last_outcome"] = outcome
        _save_state(state)
        return outcome
    finally:
        _release_lock()


def status_text() -> str:
    """给 /heartbeat 命令的状态一览。"""
    state = _load_state()
    frontier = read_frontier()
    active = sum(frontier.count(m) for m in _UNFINISHED_MARKERS)
    blocked = sum(frontier.count(m) for m in _BLOCKED_MARKERS)
    lines = ["心跳状态："]
    if not frontier.strip():
        lines.append(f"  frontier: 未种方向（{FRONTIER_FILE} 为空/不存在）→ 心跳静默")
    else:
        lines.append(f"  frontier: {active} 个活跃项，{blocked} 个停牌项")
    if _is_paused(state):
        lines.append("  ⏸ 已自暂停（连续无推进）——改动 frontier.md 后自动恢复")
    last = state.get("last_run")
    if last:
        stamp = datetime.fromtimestamp(last).strftime("%m-%d %H:%M")
        lines.append(f"  上次: {stamp} — {state.get('last_outcome', '?')}")
    lines.append(f"  今日已跑: {state.get('runs_today', 0)}/{HEARTBEAT.max_runs_per_day} 次")
    digest = pending_digest()
    lines.append(f"  待送摘要: {'有（' + str(len(digest)) + ' 字符，下轮对话注入）' if digest else '无'}")
    with contextlib.suppress(Exception):
        from .work_receipts import latest_pending_delivery

        delivery = latest_pending_delivery()
        lines.append(
            "  待用户处置: "
            + (
                f"{delivery.work_id}（/heartbeat accept|revise|reject）"
                if delivery
                else "无"
            )
        )
    lines.append("  手动触发: /heartbeat run；调度安装见 docs/heartbeat.md")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys

    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="CTGents 心跳：无人期自主推进探索前沿")
    parser.add_argument("--loop", type=int, metavar="SECONDS",
                        help="常驻循环模式，每 SECONDS 秒一跳（不给则单次退出）")
    parser.add_argument("--force", action="store_true", help="跳过每日上限/自暂停")
    parser.add_argument("--status", action="store_true", help="只看状态，不跑")
    args = parser.parse_args(argv)

    if args.status:
        print(status_text())
        return

    stamp = lambda: datetime.now().strftime("%H:%M:%S")  # noqa: E731
    if args.loop:
        print(f"[{stamp()}] 心跳循环启动，每 {args.loop}s 一跳（Ctrl+C 停）")
        while True:
            print(f"[{stamp()}] {run_once(force=args.force)}")
            time.sleep(max(args.loop, 60))
    else:
        print(f"[{stamp()}] {run_once(force=args.force)}")


if __name__ == "__main__":
    main()
