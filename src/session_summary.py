"""会话摘要生成 — LLM 语义摘要（话题/脉络/未竟事项）+ 规则提取兜底。

在 _finalize_session 中被调用。两层：
  - LLM 层（Flash 一发 chat_non_stream）：干净话题（中英文按对话原文说法）、
    对话脉络、未竟事项——"接着做"的钩子，规则提取抓不到。失败自动退回规则层。
  - 规则层（零成本、离线可用）：决策（remember/task_done/need_user）与产出文件
    永远走规则提取——它们是客观可机械提取的事实，不该交给 LLM 复述（事实给牙）。

与已删除的「LLM 收割」的边界：收割是自信重写用户画像/记忆断言（churn + 越写越歪），
本模块只 append-only 写导航索引文件、不改旧内容，错了顶多索引质量差一档。

写入 knowledge/sessions/{session_id}.md；新会话经 build_sessions_index() 把
「日期·话题」存在性索引放进前缀（按名认得 → search_sessions 取详情）。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .params import SUMMARY

logger = logging.getLogger(__name__)

_KNOWLEDGE_SESSIONS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "sessions"

_EN_TECH_PATTERN = re.compile(
    r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b'  # CamelCase
    r'|\b[A-Z]{2,}(?:\d+)?\b'           # 缩写 (LLM, API, GPT4)
    r'|\b[a-z]+(?:[-_][a-z]+)+\b'       # kebab/snake_case
    r'|\b[a-z]+\.[a-z]+\b'              # module.path
)


def _extract_topics(messages: list[dict]) -> list[str]:
    """从消息中提取话题关键词。优先级：remember 名 > task_done 摘要 > 用户首句。

    中文 n-gram 频率在无分词器时噪声太高，改为从结构化信号 + 用户消息首句提取。
    """
    topics: list[str] = []

    # ── 来源1: remember 的 name 参数（把 kebab-case 还原）──
    for m in messages:
        if m["role"] != "assistant" or "tool_calls" not in m:
            continue
        for tc in m["tool_calls"]:
            fn = tc.get("function", {})
            if fn.get("name") != "remember":
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
                name = args.get("name", "")
            except (json.JSONDecodeError, TypeError):
                continue
            if name:
                # kebab-case → 空格分隔，取前 3 段作为话题
                clean = name.replace("-", " ").replace("_", " ")
                parts = clean.split()
                if len(parts) > 3:
                    # 取最有信息量的部分（跳过通用前缀）
                    skip_prefixes = ("agent", "lesson", "user", "project", "knowledge", "reference", "strategy")
                    if parts[0] in skip_prefixes:
                        parts = parts[1:]
                topic = " ".join(parts[:4])
                if topic and topic not in topics:
                    topics.append(topic)

    # ── 来源2: task_done 摘要 ──
    for m in messages:
        if m["role"] != "assistant" or "tool_calls" not in m:
            continue
        for tc in m["tool_calls"]:
            fn = tc.get("function", {})
            if fn.get("name") != "task_done":
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
                summary = args.get("summary", "")
            except (json.JSONDecodeError, TypeError):
                continue
            if summary and len(summary) > 5:
                # 取摘要首句（到第一个句号或30字）
                first = summary.split("。")[0].split("：")[-1][:30]
                if first and first not in topics:
                    topics.append(first)

    # ── 来源3: 用户消息中的技术英文术语 ──
    all_text = " ".join(
        (m.get("content") or "") for m in messages
        if m["role"] in ("user", "assistant")
    )
    en_terms = _EN_TECH_PATTERN.findall(all_text)
    en_counter = Counter(en_terms)
    for t, c in en_counter.most_common(8):
        if c >= 2 and t.lower() not in ("ok", "ll", "pm", "am", "appdata") and t not in topics:
            topics.append(t)

    return topics[:10]


def _extract_decisions(messages: list[dict]) -> list[dict]:
    """从工具调用中提取关键决策。"""
    decisions: list[dict] = []
    for m in messages:
        if m["role"] != "assistant" or "tool_calls" not in m:
            continue
        for tc in m["tool_calls"]:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue

            if name == "remember":
                topic = args.get("name", "").replace("-", " ")
                decisions.append({
                    "type": "记忆",
                    "detail": f"{args.get('type', '?')}: {topic}"[:120],
                })
            elif name == "task_done":
                decisions.append({
                    "type": "任务完成",
                    "detail": args.get("summary", "")[:150],
                })
            elif name == "need_user":
                decisions.append({
                    "type": "待决策",
                    "detail": args.get("question", "")[:150],
                })
            elif name == "update_plan":
                # 不算决策，但算事件
                pass

    return decisions


def _extract_files(messages: list[dict]) -> list[str]:
    """从工具调用中提取修改/产出的文件路径。"""
    paths = set()
    for m in messages:
        if m["role"] != "assistant" or "tool_calls" not in m:
            continue
        for tc in m["tool_calls"]:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name not in ("write_file", "replace_in_file"):
                continue
            try:
                args = json.loads(fn.get("arguments", "{}"))
                p = args.get("path", "")
                if p:
                    paths.add(p)
            except (json.JSONDecodeError, TypeError):
                continue

    # 排序：src 优先，然后按路径
    return sorted(paths)


def _make_summary_text(
    topics: list[str],
    decisions: list[dict],
    files: list[str],
    user_messages: list[str],
) -> str:
    """生成自然语言摘要段落。"""
    parts = []

    # 话题
    if topics:
        parts.append(f"主要话题：{'、'.join(topics[:6])}。")

    # 用户讲了什么（取最长的几条）
    if user_messages:
        # 取用户消息的首句或摘要
        key_msgs = [m for m in user_messages if len(m) > 10]
        if len(key_msgs) > 3:
            key_msgs = [key_msgs[0]] + key_msgs[-2:]
        summaries = []
        for msg in key_msgs:
            s = msg.replace("\n", " ")[:100]
            summaries.append(f"「{s}」")
        parts.append("用户关切：" + " → ".join(summaries))

    # 决策
    if decisions:
        unique = []
        seen = set()
        for d in decisions:
            if d["detail"] not in seen:
                unique.append(d)
                seen.add(d["detail"])
        for d in unique[:5]:
            parts.append(f"{d['type']}：{d['detail']}。")

    # 文件
    if files and len(files) <= 8:
        parts.append(f"涉及文件：{'、'.join(files)}。")
    elif files:
        parts.append(f"涉及 {len(files)} 个文件（含{'、'.join(files[:5])} 等）。")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# LLM 语义摘要层（Flash 一发调用；失败 → 规则层兜底）
# ═══════════════════════════════════════════════════════════════

_LLM_SUMMARY_PROMPT = """你是会话归档员。根据对话文字稿输出 JSON（只输出 JSON，不要别的）：
{"topics": [...], "narrative": "...", "unfinished": "..."}

- topics: 3~6 个话题词，用对话里的原文说法（论文名/系统名/中文短语都行），\
要具体可检索，禁止 "讨论"/"代码"/"LLM" 这类泛词。
- narrative: 2~3 句话讲清这场对话干了什么、结论是什么。
- unfinished: 聊到一半没做完/说好下次继续的事，一句话；没有就空字符串。\
只写对话里真实出现的，不要推测。"""


def _build_digest(messages: list[dict]) -> str:
    """把消息列表浓缩成喂给摘要 LLM 的文字稿。

    用户消息基本保全（话题主要在这），助手正文截头，工具调用只留名字+关键参数，
    工具结果不进稿（体积大、话题密度低）。超长保头尾、中间标记省略——摘要要的是
    "这场聊了什么/最后停在哪"，恰好在两端。
    """
    lines: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user":
            lines.append(f"用户: {content[:500]}")
        elif role == "assistant":
            if content.strip():
                lines.append(f"助手: {content[:300]}")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                hint = args.get("path") or args.get("query") or args.get("name") or ""
                lines.append(f"[工具 {fn.get('name', '?')} {str(hint)[:60]}]")
    digest = "\n".join(lines)
    cap = SUMMARY.digest_max_chars
    if len(digest) > cap:
        head, tail = int(cap * 0.6), int(cap * 0.35)
        digest = digest[:head] + "\n…（中间省略）…\n" + digest[-tail:]
    return digest


def _llm_summarize(messages: list[dict]) -> dict | None:
    """Flash 一发生成 {topics, narrative, unfinished}。任何失败返回 None（规则层兜底）。"""
    digest = _build_digest(messages)
    if len(digest) < 80:  # 太短的会话不值一次调用，规则层足够
        return None
    try:
        from .llm import AVAILABLE_MODELS
        backend = AVAILABLE_MODELS["flash"]
        content, _ = backend.chat_non_stream(
            [{"role": "system", "content": _LLM_SUMMARY_PROMPT},
             {"role": "user", "content": digest}],
            on_token=lambda _t: None,
            # Flash 思考常开，reasoning 也占 completion 额度——给足空间别把 JSON 截了
            max_tokens=2000,
        )
        if not content:
            return None
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(content[start:end + 1])
        topics = [str(t).strip() for t in data.get("topics", []) if str(t).strip()]
        if not topics:
            return None
        return {
            "topics": topics[:8],
            "narrative": str(data.get("narrative", "")).strip(),
            "unfinished": str(data.get("unfinished", "")).strip(),
        }
    except Exception as e:
        logger.warning("LLM 会话摘要失败，退回规则提取: %s", e)
        return None


def extract_summary(messages: list[dict], use_llm: bool | None = None) -> dict:
    """从消息列表中提取摘要数据。返回 dict，含 topics/decisions/files/text/unfinished。

    决策/产出文件永远走规则提取（客观事实）；话题/脉络/未竟事项优先 LLM，
    失败或 use_llm=False（None=读 CTG_SUMMARY_LLM）时退回规则版。
    """
    user_messages = [
        m.get("content", "") for m in messages if m["role"] == "user"
    ]
    decisions = _extract_decisions(messages)
    files = _extract_files(messages)

    if use_llm is None:
        use_llm = SUMMARY.use_llm
    llm = _llm_summarize(messages) if use_llm else None

    if llm:
        topics = llm["topics"]
        # 脉络 = LLM 叙述 + 用户原话引用（后者保住中文原词，供 search_sessions 命中）
        rule_text = _make_summary_text([], [], [], user_messages)
        text = llm["narrative"] + ("\n" + rule_text if rule_text else "")
        unfinished = llm["unfinished"]
    else:
        topics = _extract_topics(messages)
        text = _make_summary_text(topics, decisions, files, user_messages)
        # 规则层的未竟信号：最后一次 need_user 的提问（问了没答完 = 挂起的决策）
        unfinished = next(
            (d["detail"] for d in reversed(decisions) if d["type"] == "待决策"), "")

    return {
        "topics": topics,
        "decisions": decisions,
        "files": files,
        "text": text,
        "unfinished": unfinished,
        "source": "llm" if llm else "rules",
    }


def write_summary(session_id: str, summary: dict) -> str | None:
    """将会话摘要写入 knowledge/sessions/{session_id}.md。返回写入路径，跳过则 None。"""
    # 空会话不写
    if not summary.get("topics") and not summary.get("decisions") and not summary.get("files"):
        return None

    topics_str = ", ".join(summary.get("topics", []))
    files_str = "\n".join(f"- `{f}`" for f in summary.get("files", [])) or "（无）"
    decisions_str = ""
    for d in summary.get("decisions", []):
        decisions_str += f"- [{d['type']}] {d['detail']}\n"

    # 解析时间戳
    try:
        dt = datetime.strptime(session_id, "%Y-%m-%d-%H%M%S")
        date_str = dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        date_str = session_id

    content = f"""# 会话 {session_id}
- 时间: {date_str}
- 话题: {topics_str or '（未识别）'}

## 对话脉络
{summary.get('text', '')}

## 未竟事项
{summary.get('unfinished') or '（无）'}

## 关键决策
{decisions_str or '（未检出）'}

## 产出文件
{files_str}
"""

    _KNOWLEDGE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _KNOWLEDGE_SESSIONS_DIR / f"{session_id}.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


def summarize_session(messages: list[dict], session_id: str) -> str | None:
    """一站式：提取 + 写入。返回写入路径或 None。"""
    summary = extract_summary(messages)
    return write_summary(session_id, summary)


# ═══════════════════════════════════════════════════════════════
# 跨会话搜索（新会话中按话题唤醒历史摘要）
# ═══════════════════════════════════════════════════════════════

def _load_summary(path: Path) -> dict | None:
    """解析单个摘要 Markdown 文件。返回 {session_id, topics, text, path} 或 None。"""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # 提取话题行
    topics_str = ""
    m = re.search(r'- 话题:\s*(.+)', content)
    if m:
        topics_str = m.group(1).strip()

    # 提取对话脉络段
    text = ""
    m = re.search(r'## 对话脉络\n(.+?)(?=\n## |\Z)', content, re.DOTALL)
    if m:
        text = m.group(1).strip()

    # 提取未竟事项（占位「（无）」视为空；旧格式没有此栏 → 空）
    unfinished = ""
    m = re.search(r'## 未竟事项\n(.+?)(?=\n## |\Z)', content, re.DOTALL)
    if m:
        unfinished = m.group(1).strip()
        if unfinished == "（无）":
            unfinished = ""

    session_id = path.stem
    return {
        "session_id": session_id,
        "topics": topics_str,
        "text": text,
        "unfinished": unfinished,
        "path": str(path),
    }


def search_sessions(query: str, top_n: int = 3) -> list[dict]:
    """搜索历史会话摘要，返回按相关度排序的 top_n 条。

    匹配策略：query 中的关键词在摘要的「话题」+「对话脉络」中的命中密度。
    返回 [{session_id, topics, text, path, score}, ...]。
    """
    if not _KNOWLEDGE_SESSIONS_DIR.exists():
        return []

    # 清洗 query：分词（中英文分别处理）
    # 中文：取 2-4 字片段；英文：取单词
    query_terms: list[str] = []
    # 英文单词
    query_terms.extend(
        w.lower() for w in re.findall(r'[a-zA-Z]+', query)
        if len(w) >= 2 and w.lower() not in ("ok", "ll")
    )
    # 中文 2-3 字片段
    cn_only = re.sub(r'[^\u4e00-\u9fff]', '', query)
    for n in (2, 3):
        for i in range(len(cn_only) - n + 1):
            seg = cn_only[i:i + n]
            if seg not in query_terms:
                query_terms.append(seg)

    if not query_terms:
        return []

    # 加载所有摘要
    summaries: list[dict] = []
    for f in sorted(_KNOWLEDGE_SESSIONS_DIR.glob("*.md"), reverse=True):
        s = _load_summary(f)
        if s:
            summaries.append(s)

    # 计分
    scored: list[tuple[float, dict]] = []
    search_text = query.lower()
    for s in summaries:
        score = 0.0
        combined = (s.get("topics", "") + " " + s.get("text", "")).lower()

        # 完整 query 匹配（权重最高）
        if search_text in combined:
            score += 5.0

        # 各词条命中
        for term in query_terms:
            if term.lower() in combined:
                # 长词条得分更高
                score += len(term) * 0.5

        # 归一化（避免长摘要占便宜）
        score = score / max(len(combined.split()), 1) * 100

        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_n]]


# ═══════════════════════════════════════════════════════════════
# 前缀情景索引（跨会话记忆的触发环）
# ═══════════════════════════════════════════════════════════════

def build_sessions_index() -> str | None:
    """最近 N 场会话的存在性索引，进前缀（会话开始建一次，缓存安全）。

    一行一场「日期 · 话题」，近期几场附未竟事项。哲学同记忆索引的两级结构：
    索引只解决"认得聊过"（识别，模型擅长），详情靠 search_sessions（回忆，模型不擅长
    ——所以不能指望它无提示地想起去调工具）。见 [[rule-placement-three-layers]]。
    """
    if not _KNOWLEDGE_SESSIONS_DIR.exists():
        return None
    files = sorted(_KNOWLEDGE_SESSIONS_DIR.glob("*.md"), reverse=True)
    lines: list[str] = []
    for i, f in enumerate(files[:SUMMARY.index_sessions]):
        s = _load_summary(f)
        if not s:
            continue
        topics = s.get("topics", "")
        if not topics or topics == "（未识别）":
            continue
        date = s["session_id"][:10]
        line = f"  {date} · {topics[:110]}"
        if i < SUMMARY.index_unfinished and s.get("unfinished"):
            line += f"｜未竟: {s['unfinished'][:80]}"
        lines.append(line)
    if not lines:
        return None
    header = ("最近会话（跨会话记忆）——用户提到其中话题时，先用 search_sessions "
              "搜详情（含产出文件，可 read_file 接续），别凭印象重来：")
    return header + "\n" + "\n".join(lines)
