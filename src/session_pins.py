"""会话钉板(session pinboard):本场会话内"绝不能忘"的决定/约束。

为什么存在:长会话里中途产生的决定会沉进 log 中段(注意力衰减区)被忘/被推翻。
钉板把 ≤N 条一句话决定钉在每轮消息的尾部(高注意力近因区),原地刷新。

与邻居的分工:
- 跟记忆(memory/*.md)不同:钉板是内存易失、本场可见、关窗蒸发;记忆跨会话、按需 recall。
- 跟 current.md 不同:那是文件落地的流程步骤;钉板是会话内结论,不落文件。

封顶规则(整条,绝不切某条中间):每条写入侧限长(超长截到上限);总数超上限踢最旧整条。
缓存安全:render_tail 渲染成 role=system 消息挂 log 尾,send() 自动搬到 API 末尾,不碰 prefix。
"""

from __future__ import annotations

from .params import PINBOARD

# 钉板消息在 log 中的定位标记(用于轮内原地替换刷新,见 llm.py)。
PINBOARD_MARKER = "📌 本会话已钉住"

# 本进程内存态:本场会话的 pin 列表。每项 {"text": str, "durable": bool}。
_pins: list[dict] = []


def _truncate(text: str) -> str:
    """限短:超过每条上限即截到上限并加省略号(保证后续渲染零截断)。"""
    text = text.strip()
    if len(text) > PINBOARD.max_chars:
        return text[: PINBOARD.max_chars - 1] + "…"
    return text


def add_pin(text: str, durable: bool = False) -> str:
    """钉一条:限短 + 去重 + 总量封顶(踢最旧整条)。返回给 agent 的反馈。"""
    text = _truncate(text)
    if not text:
        return "pin 内容为空,未钉。"
    for p in _pins:
        if p["text"] == text:
            p["durable"] = p["durable"] or durable
            return f"已存在,未重复钉:{text}"
    _pins.append({"text": text, "durable": durable})
    evicted = ""
    while len(_pins) > PINBOARD.max_items:
        evicted = _pins.pop(0)["text"]  # 踢最旧整条,绝不切中间
    msg = f"已钉({len(_pins)}/{PINBOARD.max_items}):{text}"
    if evicted:
        msg += f"\n(已淘汰最旧一条:{evicted})"
    return msg


def remove_pin(text: str) -> str:
    """按文本(子串)取下一条 — 决定失效/完成时调用。"""
    needle = text.strip()
    for i, p in enumerate(_pins):
        if needle in p["text"]:
            return f"已取下:{_pins.pop(i)['text']}"
    return f"未找到匹配的 pin:{text}"


def list_pins() -> list[dict]:
    """返回当前 pin 列表副本。"""
    return [dict(p) for p in _pins]


def clear_pins() -> None:
    """清空钉板(用于 /new 开新会话)。"""
    _pins.clear()


def render_tail() -> str | None:
    """渲染钉板为尾部系统消息内容;空则返回 None。"""
    if not _pins:
        return None
    lines = [f"{PINBOARD_MARKER}(本会话内有效,勿忘勿违背):"]
    for i, p in enumerate(_pins, 1):
        lines.append(f"  {i}. {p['text']}")
    return "\n".join(lines)


def is_pinboard_msg(msg: dict) -> bool:
    """判断一条 log 消息是否是钉板消息(轮内刷新定位用)。"""
    return msg.get("role") == "system" and PINBOARD_MARKER in (msg.get("content") or "")
