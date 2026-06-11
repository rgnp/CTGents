"""引用即取证：扫最终回复里的代码引用，没在本会话见过 referent 就提示。

治 ④可信 的"编造"②里**可检查的那一片**，两类引用形态：
- `path:line` 行号引用：引用了行号却全程没读/grep/改过该文件 → 很可能凭印象编。
- 反引号标识符（`snake_case` / `*.py`）：agent 谈论自身架构/代码的主要形态——
  曾整段描述 completion_audit 的设计然后宣布"这层我们没有"。代码体引用一个
  上下文里从没出现过的名字 → 要么凭印象编、要么是新提议（措辞留给 agent 判断）。
编造的其余部分（API/库行为、概念过度自信、数字断言——合法算术必然误报）是
判断题，留三态标注纪律（AGENTS.md），不在此机械化——同 recall 硬规则被否决的判别器。

与 completion_audit 的区别：耦合**松**——靠"被引 referent 出现在 agent 可见上下文里"
(工具读取 + 预读 + 用户粘贴 + 前缀/系统消息 + 注册工具名，见 _context_text)判
grounded，不依赖某个精确输出 marker，故格式微调不破它，无需 C16 契约测试。
保守：子串命中即算 grounded（宁漏判不误报；nudge 最怕狼来了）。
"""
from __future__ import annotations

import re

# `path:line` 引用：路径以代码/文档扩展名结尾 + 冒号 + 行号（与 main 预读扩展名对齐）。
# 只抓带行号的形式：裸文件名满天飞，抓了全是噪音 → precision 优先。
_CITE_RE = re.compile(
    r"([\w./\\-]+\.(?:py|md|txt|json|ya?ml|toml|cfg|ini|js|ts)):\d+"
)

# 反引号标识符：`token` 整体包裹的单个标识符（含点路径）。再过滤出"像代码引用"
# 的强形态（含下划线 / .py 结尾）——泛词（`recall`）和带空格的命令不抓，precision 优先。
_IDENT_RE = re.compile(r"`([A-Za-z_][\w.]*)`")

_NUDGE = (
    "⚠️ 取证自检：你在回复里给了 {files} 的具体行号，但本会话的工具活动里没有"
    "读/grep/改过它——这可能是凭印象编的。先 read_file 确认，或在措辞里标明是推测。"
)

_IDENT_NUDGE = (
    "⚠️ 取证自检：你的回复用代码体引用了 {names}，但它没在本会话的上下文里出现过——"
    "若你是在引用现有代码/机制，可能是凭印象编的，先 grep_code/read_file 取证；"
    "若是你新提议的命名，忽略本提示。"
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


def _extract_identifiers(text: str) -> set[str]:
    """抽出反引号包裹的强形态标识符：含下划线的名字 或 *.py 文件名。

    泛指 dunder（`__init__` 等）排除——讨论通用 Python 时高频出现，误报多。
    """
    out: set[str] = set()
    for m in _IDENT_RE.finditer(text):
        name = m.group(1)
        if name.startswith("__"):
            continue
        if "_" in name or name.endswith(".py"):
            out.add(name)
    return out


def _known_tool_names() -> set[str]:
    """注册工具名表——工具 schema 每轮都在 agent 眼前，引用它们天然 grounded。"""
    try:
        from .tools._tool_meta import TOOL_LABELS
        return set(TOOL_LABELS)
    except Exception:
        return set()


def _context_text(log: list[dict]) -> str:
    """取证 haystack —— agent 能看到的上下文文本：tool_calls 名字+参数 + tool 结果
    + **user 消息** + **system 消息**（前缀的派生机制索引/AGENTS.md、挂尾的记忆索引
    都是 agent 可见的合法取证源——按索引谈论 `_inject_*` 机制不该被误报）。

    grounded = 被引 referent 在 agent 可见的上下文里——不管来自工具读取、main 的
    preread / 用户直接粘贴（预读把文件内容拼进 user 消息、不入 tool 消息，故必须收
    user 内容，否则预读过的文件会被误报"没读过"）。不收 assistant 自己的话——
    否则它能用自己的叙述给自己的引用背书。
    """
    parts: list[str] = []
    for msg in log:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name"):
                parts.append(fn["name"])
            if fn.get("arguments"):
                parts.append(fn["arguments"])
        if msg.get("role") in ("tool", "user", "system"):
            parts.append(msg.get("content") or "")
        if msg.get("_tool_name"):
            parts.append(msg["_tool_name"])
    return "\n".join(parts)


def audit_citations(log: list[dict]) -> str | None:
    """最终回复引用了 path:line 或代码体标识符，但 referent 全程没进过上下文
    → 返回提示，否则 None。两类各自成段，可同时触发。
    """
    final_text = _last_assistant_text(log)
    cites = _extract_citations(final_text)
    idents = _extract_identifiers(final_text)
    if not cites and not idents:
        return None
    haystack = _context_text(log)
    nudges: list[str] = []
    ungrounded_paths = sorted(c for c in cites if c not in haystack)
    if ungrounded_paths:
        nudges.append(_NUDGE.format(files="、".join(ungrounded_paths)))
    known_tools = _known_tool_names()
    ungrounded_idents = sorted(
        i for i in idents if i not in known_tools and i not in haystack
    )
    if ungrounded_idents:
        nudges.append(_IDENT_NUDGE.format(names="、".join(f"`{i}`" for i in ungrounded_idents)))
    return "\n".join(nudges) if nudges else None
