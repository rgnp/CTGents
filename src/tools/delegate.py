"""delegate——派一次性任务给干净上下文 worker（首用例：文献调研）。

形态（用户拍板，2026-07-03）：一个工具，不是多 agent 系统。
  主 agent --delegate(brief, output_file)--> worker（全新 CacheContext + 工具子集
  + 独立请求预算）--> 产出落文件 --> 机械出处闸（delegate_gate，代码不是 LLM）
  --> 主 agent 只拿到"路径 + 一句话结论 + 闸结果"。

可信交接的三条铁律：
  1. worker 的详细断言不进主对话——主 agent 要引用细节必须自己 read_file（referent
     交接，防二手断言穿一手外衣）；
  2. 闸 fail-closed：不过闸先打回 worker 重试（gate_retries 次），仍不过就把
     "未通过 + 问题清单"如实返回，绝不静默放行；
  3. 审计是代码闸不是"审计 agent"——LLM 审 LLM 共享盲区且默认盖章（outcome loop
     实测 LLM 评分者对可机械判定的标准目测放行）。

隔离清单（为什么 worker 不污染主会话）：
  - 全新 CacheContext（主会话 log 与任务状态不进入 worker 请求）；
  - session_id=""（不落 sessions/ 存档）、track_stats=False（不碰主会话缓存统计）、
    on_progress 不传（不触发主会话存盘）；
  - tracker 会话指针 try/finally 恢复（run_conversation 会无条件覆盖它）；
  - 已知可接受的串扰：reset_storm/reset_safe_stats 清掉主轮窗口（性能与展示级，
    非正确性）；worker 幻觉调用子集外工具会真执行（execute_tool 无 per-call 白名单，
    v1 靠 schema 不给 + prompt 压概率，v2 方向=线程局部 allowed-set）。
  - parallel_safe 保持 False（默认）：delegate 必须在主 turn 线程串行执行，
    嵌套 run_conversation 才是单线程重入；标 SAFE 会被 eager 线程池预跑。
"""

from __future__ import annotations

import json
from pathlib import Path

# 注意：不要在模块顶层 import ..llm——本模块在 tools/__init__._init_registry()
# 里被导入，那一刻 tools 包还没定义完 get_tools/execute_tool；llm.py 顶层恰好
# from .tools import 它们，环一闭就 ImportError（故障隔离会把 delegate 静默隔离掉）。
# 统一在调用点惰性导入（也让测试 monkeypatch src.llm.run_conversation 天然生效）。
from .. import tracker
from ..cache_context import CacheContext
from ..delegate_gate import format_gate_feedback, gate_check
from ..params import DELEGATE
from ..paths import CORE_ROOT, WORKSPACE_ROOT, resolve_runtime_path
from ._toolkit import Toolkit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# worker 进度出口：默认 print（REPL 实时可见；TUI 下 stdout 被接管、静默无害，
# 同 llm.py "⚡ [Eager]" 惯例）。TUI 以后要显示 worker 活动，set_progress_sink 接管。
_progress_sink = print


def set_progress_sink(sink) -> None:
    """替换 worker 进度输出回调（TUI 等前端接管用）。"""
    global _progress_sink
    _progress_sink = sink


_WORKER_SYSTEM = """你是一次性调研 worker：干净上下文、无人值守、跑完即散。
委派你的主 agent 只会看到你的产出文件和最后一条结论，中间过程不会被任何人读。

硬规则：
1. 产出必须用 write_file 写入 {output_file}——这是唯一交付物，没写文件 = 什么都没交付。
2. 产出/报告里的每个事实断言必须带来源 URL，且 URL 必须来自你本次搜索结果或 read_page，
   禁止凭记忆补 URL。
3. 标注反映核实程度：[已核] = 你真调过 read_page 读了原文；只看了搜索摘要的标
   [未核·仅摘要]。你的产出会过机械出处闸——闸核的是工具调用记录，不看措辞。
   格式硬约束：[已核] 所在的**同一行**必须带该来源 URL（闸按行核，URL 后置汇总
   到"参考文献"一节会被拦）。表格里用 [已核] 就把 URL 放进同一行单元格。
4. 你无法向任何人提问。信息确认不了就降级标注，不要停下来等回复。
5. 请求预算 {budget} 次。收尾时用一条纯文本回复给出 ≤3 行的最终结论（不调工具）。"""


def _worker_tools() -> list[dict]:
    """Worker 工具子集：来自 DELEGATE.worker_tools，永不含 delegate 自身（防递归）。"""
    from . import get_tools_subset
    names = frozenset(
        n.strip() for n in DELEGATE.worker_tools.split(",")
        if n.strip() and n.strip() != "delegate"
    )
    return get_tools_subset(names)


def _validate_output_path(output_file: str) -> tuple[Path | None, str]:
    """路径校验：允许核心 docs 或个人 knowledge，拒绝代码/存档/git。"""
    try:
        target = resolve_runtime_path(output_file, _PROJECT_ROOT)
        if target.is_relative_to(WORKSPACE_ROOT):
            rel = target.relative_to(WORKSPACE_ROOT)
        else:
            rel = target.relative_to(CORE_ROOT)
    except (ValueError, OSError):
        return None, (
            f"⛔ delegate 拒绝: output_file 必须在核心项目或个人 workspace 内，收到 {output_file!r}。\n"
            "正道：1. 调研产出推荐 knowledge/<领域>/... 或 knowledge/paper/<论文名>/analysis.md；\n"
            "2. 其他文档可用 docs/ 下路径。"
        )
    first = rel.parts[0] if rel.parts else ""
    if first in ("src", "sessions", ".git"):
        return None, (
            f"⛔ delegate 拒绝: worker 产出不允许写入 {first}/（代码/存档/版本库不是调研交付区）。\n"
            "正道：1. 调研产出推荐 knowledge/<领域>/... 或 knowledge/paper/<论文名>/analysis.md；\n"
            "2. 其他文档可用 docs/ 下路径。"
        )
    return target, ""


tk = Toolkit()


@tk.tool(
    label="委派调研",
    group="core",
    dedup_blacklist=True,  # 有副作用（worker 写盘），不进 storm 去重、写后读缓存正确失效
    params={
        "brief": "任务简报：要查什么、判断标准、期望的产出结构",
        "output_file": "产出文件相对路径（推荐 knowledge/ 下；主agent之后自己 read_file 取细节）",
        "psyche": "可选：注入 worker 的领域 psyche 名（如 autonomous-driving）",
    },
)
def delegate(brief: str, output_file: str, psyche: str = "") -> str:
    """派调研给干净上下文worker，产出过机械出处闸返回路径+结论（细节自行read_file）。串行阻塞：一次只派一个，拿到结果再派下一个，同批多余调用会被拒绝。"""
    if not DELEGATE.enabled:
        return "⛔ delegate 已禁用（CTG_DELEGATE_ENABLED=0）。"

    out_path, err = _validate_output_path(output_file)
    if err:
        return err

    # ── worker 前缀：角色+硬规则 (+ 可选领域 psyche core 原文) ──
    prefix = [{
        "role": "system",
        "content": _WORKER_SYSTEM.format(
            output_file=output_file, budget=DELEGATE.worker_max_requests,
        ),
    }]
    if psyche:
        core_text = _read_psyche_core(psyche)
        if core_text:
            prefix.append({"role": "system", "content": core_text})

    worker_ctx = CacheContext(prefix_msgs=prefix)

    # 压缩前取证：on_tool 在工具执行前收到 (name, 解析后 args)，原样记进 evidence——
    # 搜索结果 content 进 log 前可能被头尾压缩丢中段，arguments 这层永远无损。
    evidence: list[str] = []

    def _on_tool(name: str, args: dict) -> None:
        args_json = json.dumps(args, ensure_ascii=False)
        evidence.append(f"{name} {args_json}")
        _progress_sink(f"  ↳ [worker] {name}({args_json[:80]})")

    from .. import llm as _llm

    def _run_worker(prompt: str, budget: int) -> str:
        return _llm.run_conversation(
            worker_ctx, prompt,
            on_token=lambda _t: None,   # worker 正文不刷主屏
            on_tool=_on_tool,
            session_id="",              # 不落 sessions/ 存档
            tools=_worker_tools(),
            track_stats=False,          # 不碰主会话缓存统计
            max_requests=budget,
        )

    main_sid = tracker.current_session()
    try:
        report = _run_worker(brief, DELEGATE.worker_max_requests)

        signal = worker_ctx.control_signal
        if signal == "interrupted":
            _llm.request_interrupt()  # 传染回主轮：用户按 Esc 是要停整件事，不只停 worker
            return "⏹️ delegate 被用户中断（worker 已停，主轮随后停止）。"
        if signal == "need_user":
            return (
                f"⛔ delegate 未完成: worker 声称需要用户拍板——「{worker_ctx.control_payload}」。\n"
                "可把该信息补进 brief 后重新 delegate。"
            )
        if signal == "task_done" and worker_ctx.control_payload:
            report = f"{report}\n{worker_ctx.control_payload}".strip()

        # ── 机械出处闸：fail-closed，先打回重试，仍不过如实返回 ──
        problems = gate_check(report, out_path, worker_ctx.log, "\n".join(evidence))
        for _ in range(DELEGATE.gate_retries):
            if not problems:
                break
            report = _run_worker(
                format_gate_feedback(problems, out_path),
                DELEGATE.retry_max_requests,
            )
            if worker_ctx.control_signal == "interrupted":
                _llm.request_interrupt()
                return "⏹️ delegate 被用户中断（worker 已停，主轮随后停止）。"
            problems = gate_check(report, out_path, worker_ctx.log, "\n".join(evidence))
    finally:
        # run_conversation 无条件覆盖 tracker 会话指针——恢复主会话归属
        tracker.set_session(main_sid)

    gate_line = (
        "✅ 出处闸: 通过" if not problems
        else "⛔ 出处闸: 未通过\n" + "\n".join(f"  - {p}" for p in problems)
        + "\n  ↯ 重派前先针对上面的问题清单修改 brief（明确要求 worker 对相关来源 "
          "read_page / 降级标注）——原样重派只会撞同一堵墙（worker 每次都是全新上下文，"
          "不记得上次为什么失败）。"
    )
    size = len(out_path.read_text(encoding="utf-8", errors="ignore")) if out_path.exists() else 0
    llm_calls = sum(1 for m in worker_ctx.log if m.get("role") == "assistant")
    conclusion = (report or "").strip()
    if len(conclusion) > 200:
        conclusion = conclusion[:200] + "…"
    return (
        f"{gate_line}\n"
        f"📄 产出: {output_file}（{size} 字符）——引用其中断言前先 read_file 亲自核对\n"
        f"🧾 worker 结论: {conclusion}\n"
        f"⚙️ worker 用量: {llm_calls} 次 LLM 响应"
    )


def _read_psyche_core(name: str) -> str:
    """读领域 psyche core 原文拼进 worker 前缀（纯读文件，不走 inject_psyche 的
    ctx 生命周期管理——worker 是一次性上下文，无加载/卸载概念）。
    """
    try:
        from ..psyche_bridge import _find_core_file
        path = _find_core_file(name)
        if path:
            return Path(path).read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


TOOLS_DELEGATE = tk.schemas


def execute(name: str, args: dict):
    """派发链契约：不认识的工具名返回 None（Toolkit.execute 已保证）。"""
    return tk.execute(name, args)
