"""引用即取证：扫最终回复里的 `path:line` 引用，没在本会话碰过该文件就提示。

治 ④可信 的"编造"②里**可检查的那一片**：agent 引用"foo 在 llm.py:120"是
有真值源的事实断言（磁盘 + 动作日志）。引用了行号却全程没读/grep/改过该文件
→ 很可能凭印象编的。编造的其余部分（API/库行为、概念过度自信）是判断题，
留三态标注纪律（AGENTS.md），不在此机械化——同 recall 硬规则被否决的判别器。

与 completion_audit 的区别：耦合**松**——靠"被引文件名出现在 agent 可见上下文里"
(工具读取 + 预读 + 用户粘贴，见 _context_text)判 grounded，不依赖某个精确输出
marker，故格式微调不破它，无需 C16 契约测试。
保守：basename 子串命中即算 grounded（宁漏判不误报；nudge 最怕狼来了）。
"""
from __future__ import annotations

import re

# `path:line` 引用：路径以代码/文档扩展名结尾 + 冒号 + 行号（与 main 预读扩展名对齐）。
# 只抓带行号的形式：裸文件名满天飞，抓了全是噪音 → precision 优先。
_CITE_RE = re.compile(
    r"([\w./\\-]+\.(?:py|md|txt|json|ya?ml|toml|cfg|ini|js|ts)):\d+"
)

_NUDGE = (
    "⚠️ 取证自检：你在回复里给了 {files} 的具体行号，但本会话的工具活动里没有"
    "读/grep/改过它——这可能是凭印象编的。先 read_file 确认，或在措辞里标明是推测。"
)


def _last_assistant_text(log: list[dict]) -> str:
    """最终交付给用户的那条 assistant 回复（无 tool_calls 的那条）的正文。"""
    for msg in reversed(log):
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            return msg.get("content") or ""
    return ""


def _extract_citations(text: str) -> set[str]:
    """抽出 `path:line` 引用，归一到 basename（路径形态各异，按文件名比对）。"""
    out: set[str] = set()
    for m in _CITE_RE.finditer(text):
        path = m.group(1).replace("\\", "/")
        out.add(path.rsplit("/", 1)[-1])
    return out


def _context_text(log: list[dict]) -> str:
    """取证 haystack —— agent 能看到的上下文文本：tool_calls 参数 + tool 结果
    + **user 消息内容**。

    grounded = 被引文件在 agent 可见的上下文里——不管来自工具读取、还是 main 的
    preread / 用户直接粘贴（预读把文件内容拼进 user 消息、不入 tool 消息，故必须收
    user 内容，否则预读过的文件会被误报"没读过"）。不收 assistant 自己的话——
    否则它能用自己的叙述给自己的引用背书。
    """
    parts: list[str] = []
    for msg in log:
        for tc in msg.get("tool_calls") or []:
            args = tc.get("function", {}).get("arguments")
            if args:
                parts.append(args)
        if msg.get("role") in ("tool", "user"):
            parts.append(msg.get("content") or "")
    return "\n".join(parts)


def audit_citations(log: list[dict]) -> str | None:
    """最终回复引用了 path:line，但被引文件全程没进过工具活动 → 返回提示，否则 None。"""
    cites = _extract_citations(_last_assistant_text(log))
    if not cites:
        return None
    haystack = _context_text(log)
    ungrounded = sorted(c for c in cites if c not in haystack)
    if not ungrounded:
        return None
    return _NUDGE.format(files="、".join(ungrounded))
