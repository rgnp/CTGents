"""delegate worker 产出的机械出处闸——代码判定，不引入 LLM。

worker（干净上下文的一次性子代理，见 tools/delegate.py）的报告与产出文件，
在返回主 agent 之前必须过这道闸。三项检查全部键在客观 referent 上：

  ① 交付存在：output_file 存在且不短于 DELEGATE.min_output_chars；
  ② URL grounding：报告/产出里引用的每个 URL，必须在 worker 自己的工具活动
     （搜索结果、read_page 调用、工具参数）里出现过——编造的来源过不了子串命中；
  ③ [已核] 断言：声称核实过的行必须带 URL，且该 URL 真被 read_page 读过
     （键在 tool_calls.arguments 上——参数永不被结果压缩，零误报风险）。

haystack 复用 citation_audit._context_text（同一"宁漏不误报"的子串判据，
不收 assistant 自述——worker 不能用自己的叙述给自己的引用背书）。
搜索结果 content 可能被头尾压缩丢中段，合法 URL 因此未命中时，反馈文案
引导 worker 对该 URL 调 read_page——URL 随即进入 arguments，必然过闸（自愈）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .citation_audit import _context_text
from .params import DELEGATE

# URL 提取：断在空白/右括号/中英文标点（正文里的 URL 常被这些字符收尾）
_URL_RE = re.compile(r"https?://[^\s\)\]>，。；、\"'）】》]+")

_VERIFIED_MARK = "[已核]"
# 行内代码 span（`...`）：其中的 [已核] 是对标记的"提及"不是核实"声称"——worker 修完
# 文件后自述「每处 `[已核]` 已与 URL 同行」被闸按声称打回，会拖进自指重试循环
# （2026-07-17/07-20 会话实测）。判定标记是否存在前先剥掉行内代码。
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")

# 闸反馈的固定开头——重试时反馈作为 user 消息进 worker log，而 haystack 收 user
# 消息（用户/主 agent 给的 URL 合法 grounded）。若不剔除，反馈里点名的编造 URL
# 会被闸自己"洗白"、重试必然假通过。按此前缀把闸自述从取证源里排除。
_FEEDBACK_PREFIX = "⛔ 出处闸未通过"


def _norm_url(url: str) -> str:
    """归一 URL 供比对：去尾部标点残留和尾斜杠。"""
    return url.rstrip(".,;:!?)]}>》】）'\"").rstrip("/")


def extract_urls(text: str) -> set[str]:
    """抽取文本里的全部 URL（已归一）。"""
    return {_norm_url(u) for u in _URL_RE.findall(text or "")}


def collect_read_page_urls(worker_log: list[dict]) -> set[str]:
    """从 tool_calls.arguments 抽 worker 真调过 read_page 的 URL（参数不被压缩，全量可信）。"""
    urls: set[str] = set()
    for msg in worker_log:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name") != "read_page":
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(args, dict) and args.get("url"):
                urls.add(_norm_url(str(args["url"])))
    return urls


def _url_read(cited: str, read_urls: set[str]) -> bool:
    """Cited 是否被 read_page 读过——精确命中或互为前缀（arxiv abs/pdf 等变体宽容）。"""
    return any(cited == u or cited in u or u in cited for u in read_urls)


def gate_check(
    report_text: str,
    output_file: Path,
    worker_log: list[dict],
    extra_evidence: str = "",
) -> list[str]:
    """跑三项机械检查，返回问题清单；空列表 = 过闸。

    extra_evidence：delegate 在工具执行前记录的 (name, args) 证据（压缩前取证），
    并入 haystack 进一步降低误拒。
    """
    problems: list[str] = []

    # ① 交付存在
    output_text = ""
    if not output_file.exists():
        problems.append(f"产出文件 {output_file} 不存在——交付物缺失。")
    else:
        output_text = output_file.read_text(encoding="utf-8", errors="ignore")
        if len(output_text) < DELEGATE.min_output_chars:
            problems.append(
                f"产出文件 {output_file} 仅 {len(output_text)} 字符"
                f"（低于下限 {DELEGATE.min_output_chars}）——交付物过短。"
            )

    testimony = [
        m for m in worker_log
        if not (m.get("role") == "user"
                and str(m.get("content") or "").startswith(_FEEDBACK_PREFIX))
    ]
    haystack = _context_text(testimony) + "\n" + extra_evidence
    combined = (report_text or "") + "\n" + output_text

    # ② URL grounding
    ungrounded = sorted(u for u in extract_urls(combined) if u not in haystack)
    for u in ungrounded:
        problems.append(f"URL 未在你的工具活动中出现过（疑似编造来源）: {u}")

    # ③ [已核] 断言（反引号内的 `[已核]` 是提及不是声称，剥掉行内代码后再判）
    read_urls = collect_read_page_urls(worker_log)
    for line in combined.splitlines():
        if _VERIFIED_MARK not in _CODE_SPAN_RE.sub("", line):
            continue
        line_urls = extract_urls(line)
        if not line_urls:
            problems.append(f"标了 {_VERIFIED_MARK} 但没给来源 URL: {line.strip()[:80]}")
        elif not any(_url_read(u, read_urls) for u in line_urls):
            problems.append(
                f"标了 {_VERIFIED_MARK} 但没用 read_page 读过该来源: {line.strip()[:80]}"
            )

    return problems


def format_gate_feedback(problems: list[str], output_file: Path) -> str:
    """闸未过时喂回 worker 的反馈——⛔ 原因 / 纠偏 / 编号正道（同 exec.py 门文案风格）。"""
    listing = "\n".join(f"  - {p}" for p in problems)
    return (
        f"⛔ 出处闸未通过（机械核查，不看措辞）：\n{listing}\n\n"
        "标注反映的是核实程度，不是自信程度。正道：\n"
        f"1. 未 grounding 的 URL：对它调 read_page 读到原文后重报，或删掉该引用；\n"
        f"2. 没真读过的 {_VERIFIED_MARK}：改标 [未核·仅摘要]，或补 read_page 后保留；\n"
        f"3. 产出缺失/过短：用 write_file 把完整报告写入 {output_file} 后重报。\n"
        "修正后重新给出最终结论。"
    )
