"""项目/领域知识收割 — 会话结束从对话提炼"关于这摊活"的持久事实，沉淀进记忆。

第三条机械收割渠道，与另两条对称：
- lesson.py     收 "失败模式"（什么搞砸了）
- user_model.py 收 "对用户的理解"（你是谁、你的偏好，type=user，全文注入）
- 本模块        收 "项目/领域 durable 事实"（你这摊活本身：数据集 / 约定 / 造过的工具 /
                领域门道 / 项目约束），type=knowledge → 摘要注入、需要时 recall 取详情。

为什么补这条：主动 remember 被结构性压住（前缀散文不催反加阻力 + answer-the-task 默认），
"关于你工作的事实"本来无可靠入口沉淀 → "越用越懂你的工作"在根上被饿着。机械收割（不靠
agent 临场想起）+ 从真实会话提炼（用拉出来的、非 agent 手痒推的）补上这条进货渠道。

为什么 type=knowledge 而非 user：项目知识会长大，全文每轮注入会烧 DeepSeek 前缀缓存
（头号指标）。knowledge → memory._build_context 注入一行摘要，agent 需要时 recall 深读。

为什么单文件演进而非每会话散 N 条：记忆 bloat 是已记隐患。维护单一 project-knowledge，
LLM 累积精炼、合并去重。隔离调用（同 user_model 的干净上下文）不碰前缀缓存。

防编造：严格 system prompt——只写对话里有证据的 durable 事实、保留旧的除非本次推翻、
没新东西原样返回。v1 已知限制：digest 走对话（用户话+助手最终回复），不含工具原始输出，
故纯从工具流里冒出、助手没在回复里复述的事实可能漏；靠助手通常会在回复里综合发现兜底，
真用一阵看是否需要纳入精简的工具发现。
"""

from __future__ import annotations

import logging
import time

from .params import PROJECT_KNOWLEDGE

logger = logging.getLogger(__name__)

_KNOWLEDGE_NAME = "project-knowledge"

_HARVEST_SYSTEM = (
    "你在维护一份关于「某个项目/某摊工作」的长期事实档案，目的是日后这位助手更懂这摊活、"
    "少问已知的、复用已摸清的。给你【现有档案】和【本次对话】，请输出更新后的档案。铁律：\n"
    "1) 只写对话里有明确证据的、具体且持久的事实——项目约定 / 用到的数据集或资源 / 这次造过"
    "或摸清的工具 / 领域门道 / 项目约束 / 关键路径与位置。不要写临时任务进度、不要泛泛而谈、"
    "不要纯推测；拿不准就不写。\n"
    "2) 不要写「关于这个人」的偏好（那是另一份档案）、也不要写失败教训（那是另一套）。"
    "只写「关于这摊活」的事实。\n"
    "3) 保留现有档案里的每一条，除非本次对话明确推翻或更新了它——绝不无故删除旧事实。\n"
    "4) 用简洁中文 bullet，按面分组（可用 '## 面名' 小标题）；总长不超过约 {max_chars} 字。\n"
    "5) 若本次没有任何值得新增或修改的，就原样返回现有档案。\n"
    "只输出档案正文本身（Markdown），不要解释、不要任何前后缀。"
)


def _load_knowledge() -> str:
    """读现有项目知识档案正文；不存在 / 读不动返回空串。"""
    from .tools.memory import _mem_path, _split_frontmatter
    p = _mem_path(_KNOWLEDGE_NAME)
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
        first, _, rest = text.partition("\n")
        if first.strip().lower() in ("markdown", "md"):
            text = rest
    return text.strip()[: PROJECT_KNOWLEDGE.max_body_chars * 2]


def harvest_project_knowledge(log: list[dict]) -> str | None:
    """从对话提炼 / 更新项目知识档案。返回新档案正文，或 None（跳过 / 失败 / 空）。

    跳过条件：总开关关、用户消息太少、对话精华为空。失败（网络）不抛，返回 None。
    复用 user_model 的对话精华（同一"理解靠对话不靠工具流水"口径）。
    """
    if not PROJECT_KNOWLEDGE.enabled:
        return None
    from .user_model import _conversation_digest, _count_user_messages
    if _count_user_messages(log) < PROJECT_KNOWLEDGE.min_user_messages:
        return None
    digest = _conversation_digest(log, PROJECT_KNOWLEDGE.digest_char_budget)
    if not digest.strip():
        return None

    from .llm import AVAILABLE_MODELS, RETRYABLE
    backend = AVAILABLE_MODELS["pro"]
    existing = _load_knowledge()
    system = _HARVEST_SYSTEM.format(max_chars=PROJECT_KNOWLEDGE.max_body_chars)
    payload = (f"【现有档案】\n{existing or '（空，尚无档案）'}\n\n"
               f"【本次对话】\n{digest}")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": payload}]

    last_err: Exception | None = None
    for attempt in range(PROJECT_KNOWLEDGE.harvest_retries + 1):
        try:
            content, _ = backend.chat_non_stream(
                messages, lambda _t: None, tools=None,
                max_tokens=PROJECT_KNOWLEDGE.max_body_chars * 2)
            return _clean_output(content or "") or None
        except RETRYABLE as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    logger.warning("项目知识收割调用失败（不阻塞退出）: %s", last_err)
    return None


def save_project_knowledge(body: str) -> bool:
    """写 / 覆盖项目知识档案记忆（type=knowledge → 摘要注入，recall 取详情）。"""
    if not body or not body.strip():
        return False
    from .tools.memory import _remember
    _remember(_KNOWLEDGE_NAME, body.strip(), "knowledge")
    return True


def harvest_and_save(log: list[dict]) -> str | None:
    """会话收尾入口：收割 + 落盘。返回简报字符串或 None（无更新 / 跳过 / 失败）。"""
    body = harvest_project_knowledge(log)
    if body and save_project_knowledge(body):
        return "已更新项目知识档案（下次会话索引可见，recall 取详情）。"
    return None
