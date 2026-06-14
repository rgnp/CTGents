"""每轮对话前自动检索记忆：机械触发，不靠 LLM 自觉。

MemoCue (2507.23633) 验证了"用对话内容自动生成检索策略"远比
"等 agent 想起调 recall"有效。本模块在 process_turn 中机械接线——
每轮用户输入进入 LLM 前，先跑一次记忆检索，相关记忆挂尾注入。

与 _inject_thinking_stance 同理：behavior 引导必须挂 log 尾靠 recency 生效。
"""

from __future__ import annotations

from pathlib import Path


def _prerecall_memory(user_input: str) -> str | None:
    """用用户输入搜索记忆，返回注入文本或 None。

    阈值来自 params.MEMORY.recall_min_score，低于阈值的命中不注入（防噪声）。
    最多注入 3 条（防淹没当前对话）。
    """
    from .config import MEMORY_DIR
    from .params import MEMORY as _P
    from .tools.memory import (
        _SNIPPET_CHARS,
        _score_memory,
        _split_frontmatter,
        _tokenize,
    )

    q_lower = user_input.lower().strip()
    if len(q_lower) < 3:
        return None

    mem_dir = Path(MEMORY_DIR)

    q_tokens = _tokenize(q_lower)
    scored: list[tuple[float, str, str, str, str]] = []

    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            full = f.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _split_frontmatter(full)
        if meta.get("severity"):
            continue
        s = _score_memory(q_tokens, q_lower, meta.get("name", f.stem),
                          meta.get("description", ""), body)
        if s <= _P.recall_min_score:
            continue
        snippet = body[:_SNIPPET_CHARS].replace("\n", " ")
        scored.append((s, meta.get("updated", ""), f.stem,
                       meta.get("type", "") or "", snippet))

    if not scored:
        return None

    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    top = scored[:3]

    lines = [(
        "[自动记忆检索] 以下记忆可能与当前对话相关（机械检索，不是主动 recall；"
        "如有用请用 recall 深读）："
    )]
    for _s, _u, name, mtype, snippet in top:
        tag = f" [{mtype}]" if mtype else ""
        lines.append(f"  📌 {name}{tag}: {snippet[:200]}")
    return "\n".join(lines)


_AUTO_RECALL_CACHE: dict[str, str | None] = {}
_AUTO_RECALL_CACHE_MAX = 32


def prerecall_for_turn(user_input: str) -> str | None:
    """对 user_input 做自动记忆检索（带缓存，同输入不重复搜）。"""
    key = user_input[:200]
    if key in _AUTO_RECALL_CACHE:
        return _AUTO_RECALL_CACHE[key]
    result = _prerecall_memory(user_input)
    _AUTO_RECALL_CACHE[key] = result
    if len(_AUTO_RECALL_CACHE) > _AUTO_RECALL_CACHE_MAX:
        _AUTO_RECALL_CACHE.pop(next(iter(_AUTO_RECALL_CACHE)))
    return result


def inject_prerecall(ctx, user_input: str) -> None:
    """在 ctx.log 尾部注入自动记忆检索结果（strip-then-append，缓存安全）。"""
    ctx.log[:] = [m for m in ctx.log if not m.get("_auto_recall")]
    result = prerecall_for_turn(user_input)
    if result:
        ctx.log.append({
            "role": "system", "content": result,
            "_volatile": True, "_auto_recall": True,
        })
