"""用户理解收割 — 会话结束从对话提炼"关于用户"的持久理解，沉淀进每轮注入的记忆。

与 lesson.py 对称：lesson.py 机械收割"失败模式"（type=strategy），本模块用 LLM 收割
"对用户的理解"（type=user）。两者都在 main._finalize_session 接线，都不阻塞退出。

为什么用 LLM 而非机械正则：用户的沟通风格 / 讲解口味 / 知识背景 / 在意的目标是语义信号，
正则只能抓"我要简洁"这种显式表态，抓不到"理解你的想法"。隔离调用（同 outcome.grade
的干净上下文模式）独立 messages，不碰 DeepSeek 前缀缓存。

为什么单文件演进而非每会话散 N 条：记忆 bloat 是已记隐患。维护单一 user-profile，
累积精炼；type=user → memory._build_context 每轮全文注入（_FULLBODY_TYPES），且无
severity 不会被当 lesson 跳过。用户随时可见 / 可改 / 可删（就是一个 md 文件）。

防编造：严格 system prompt（同 outcome 评分者的纪律）——只写对话里有证据的、保留旧的
除非本次推翻、没新东西原样返回。已知 v1 风险：单文件交 LLM 重写，坏会话可能漏旧事实；
靠"绝不无故删旧事实"指令 + 用户可见可纠正兜底。真用一阵看是否需要更强的合并保护。
"""

from __future__ import annotations

import logging
import time

from .params import USER_MODEL

logger = logging.getLogger(__name__)

_PROFILE_NAME = "user-profile"

_HARVEST_SYSTEM = (
    "你在维护一份关于某个用户的长期理解档案，目的是日后更懂他、按他能接受的方式与他协作。"
    "给你【现有档案】和【本次对话】，请输出更新后的档案。铁律：\n"
    "1) 只写对话里有明确证据的、具体且持久的事实——沟通风格 / 讲解偏好 / 知识背景 / "
    "在意的目标 / 工作习惯。不要写泛泛而谈、临时性、或纯推测的内容；拿不准就不写。\n"
    "2) 保留现有档案里的每一条，除非本次对话明确推翻或更新了它——绝不无故删除旧事实。\n"
    "3) 用简洁中文 bullet，按面分组（可用 '## 面名' 小标题）；总长不超过约 {max_chars} 字。\n"
    "4) 若本次没有任何值得新增或修改的，就原样返回现有档案。\n"
    "只输出档案正文本身（Markdown），不要解释、不要任何前后缀。"
)


def _count_user_messages(log: list[dict]) -> int:
    return sum(1 for m in log
              if m.get("role") == "user" and (m.get("content") or "").strip())


def _conversation_digest(log: list[dict], budget: int) -> str:
    """对话精华：用户的话（全）+ 助手回复（略），去掉工具调用噪声——理解用户靠对话，
    不靠工具调用流水。超预算取尾部（近因更代表当前理解状态）。
    """
    parts: list[str] = []
    for m in log:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            parts.append(f"用户: {content}")
        elif role == "assistant" and not m.get("tool_calls"):
            parts.append(f"助手: {content[:300]}")
    text = "\n".join(parts)
    if len(text) > budget:
        text = "…（前略）\n" + text[-budget:]
    return text


def _load_profile() -> str:
    """读现有档案正文；不存在 / 读不动返回空串。

    读写都走 memory._mem_path（同一 MEMORY_DIR 绑定，测试单点隔离）。
    """
    from .tools.memory import _mem_path, _split_frontmatter
    p = _mem_path(_PROFILE_NAME)
    if not p.exists():
        return ""
    try:
        _meta, body = _split_frontmatter(p.read_text(encoding="utf-8"))
        return body.strip()
    except Exception:
        return ""


def _clean_output(raw: str) -> str:
    """去掉可能的代码围栏 / 语言标记，硬截长度防失控（软约束在 prompt，这里是地板）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        # 去掉围栏首行可能残留的 "markdown" / "md" 语言标记
        first, _, rest = text.partition("\n")
        if first.strip().lower() in ("markdown", "md"):
            text = rest
    return text.strip()[: USER_MODEL.max_profile_chars * 2]


def harvest_user_profile(log: list[dict]) -> str | None:
    """从对话提炼 / 更新用户理解档案。返回新档案正文，或 None（跳过 / 失败 / 空）。

    跳过条件：总开关关、用户消息太少、对话精华为空。失败（网络）不抛，返回 None。
    """
    if not USER_MODEL.enabled:
        return None
    if _count_user_messages(log) < USER_MODEL.min_user_messages:
        return None
    digest = _conversation_digest(log, USER_MODEL.digest_char_budget)
    if not digest.strip():
        return None

    from .llm import AVAILABLE_MODELS, RETRYABLE
    backend = AVAILABLE_MODELS["pro"]
    existing = _load_profile()
    system = _HARVEST_SYSTEM.format(max_chars=USER_MODEL.max_profile_chars)
    payload = (f"【现有档案】\n{existing or '（空，尚无档案）'}\n\n"
               f"【本次对话】\n{digest}")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": payload}]

    last_err: Exception | None = None
    for attempt in range(USER_MODEL.harvest_retries + 1):
        try:
            content, _ = backend.chat_non_stream(
                messages, lambda _t: None, tools=None,
                max_tokens=USER_MODEL.max_profile_chars * 2)
            return _clean_output(content or "") or None
        except RETRYABLE as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    logger.warning("用户模型收割调用失败（不阻塞退出）: %s", last_err)
    return None


def save_user_profile(body: str) -> bool:
    """写 / 覆盖用户档案记忆（type=user → 每轮全文注入）。空内容不写。返回是否写入。"""
    if not body or not body.strip():
        return False
    from .tools.memory import _remember
    _remember(_PROFILE_NAME, body.strip(), "user")
    return True


def harvest_and_save(log: list[dict]) -> str | None:
    """会话收尾入口：收割 + 落盘。返回简报字符串或 None（无更新 / 跳过 / 失败）。"""
    body = harvest_user_profile(log)
    if body and save_user_profile(body):
        return "已更新用户理解档案（下次会话自动注入）。"
    return None
