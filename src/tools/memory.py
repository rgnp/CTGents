"""记忆系统：agent 可以 remember / recall / forget 跨会话知识。"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from pathlib import Path

from ..config import ARCHIVE_DIR, MEMORY_DIR
from ..params import MEMORY as _PARAMS

# recall 片段长度(结构性常量,留本模块)。
_SNIPPET_CHARS = 200
# 分词:ASCII alnum 词 + 中文连续块(下方切 bigram)。
_TOKEN_ASCII = re.compile(r"[a-z0-9]+")
_TOKEN_CJK = re.compile(r"[一-鿿]+")
_RETIRED_DIR_NAME = "_retired"

# ── 回忆双向扩词：中↔英互搜 ——
# 搜"用户"时自动加"user" token，避免中文查询无法命中英文记忆（反之亦然）。
# 只覆盖领域内高频词，不加通用词典——维护成本可控，不拖累检索精度。
_TRANSLITERATE: dict[str, list[str]] = {
    "用户": ["user"], "偏好": ["preference", "prefers"], "画像": ["profile"],
    "习惯": ["habit", "pattern"], "记忆": ["memory"],
    "策略": ["strategy"], "知识": ["knowledge"],
    "能力": ["capacity", "capability"],
    "搜索": ["search", "retrieval"], "压缩": ["compact", "compression"],
    "对话": ["session", "conversation"], "任务": ["task"],
    "代码": ["code"], "测试": ["test"],
    "总结": ["summary"],
    "user": ["用户"], "preference": ["偏好"], "profile": ["画像"],
    "memory": ["记忆"], "strategy": ["策略"], "knowledge": ["知识"],
    "capacity": ["能力"], "capability": ["能力"],
    "session": ["对话"], "task": ["任务"],
    "compact": ["压缩"], "search": ["搜索"],
    "habit": ["习惯"], "pattern": ["习惯"],
}

# ── 记忆索引缓存（避免每次请求重复读文件） ──
_context_cache: str | None = None
_context_dirty: bool = True


def _is_active(meta: dict[str, str]) -> bool:
    return meta.get("status", "active") == "active" and not meta.get("superseded_by")


def _set_metadata_fields(raw: str, fields: dict[str, str]) -> str:
    """Update or append flattened metadata fields without rewriting the body."""
    if not raw.startswith("---"):
        return raw
    lines = raw.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return raw
    for key, value in fields.items():
        replacement = f"  {key}: {value}"
        found = next(
            (i for i in range(1, close) if lines[i].strip().startswith(f"{key}:")),
            None,
        )
        if found is not None:
            lines[found] = replacement
        else:
            lines.insert(close, replacement)
            close += 1
    return "\n".join(lines)



def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 frontmatter，返回 (扁平键值对, 正文)。

    闭合分隔符必须是「单独成行的 ---」，逐行匹配而非 find("---") 子串查找——
    否则 description 或正文里出现 '---' 会被误当成闭合，导致 type 丢失、正文
    串入 metadata、索引摘要丢空。键值按行 strip 后取首个冒号前后，故顶层的
    name/description 与 metadata 下缩进的 type 都进同一扁平字典（无命名冲突）。
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw in lines[1:close]:
        line = raw.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    body = "\n".join(lines[close + 1:]).strip()
    return meta, body


def _extract_distilled(text: str) -> list[str]:
    """从 frontmatter 中提取所有 distilled: 行（不依赖 _split_frontmatter 扁平 dict）。"""
    if not text.startswith("---"):
        return []
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return []
    result: list[str] = []
    for raw in lines[1:close]:
        line = raw.strip()
        if line.startswith("distilled:"):
            result.append(line.split(":", 1)[1].strip())
    return result


# ── 上下文注入用类型 ──
# user（用户画像）→ 全文注入前缀、不截断：字数有限、是"懂你"的常驻地基，每轮都该看全。
# 其余（strategy/knowledge/reference/lesson）→ 仅列主题名（name），不带描述：
#   描述作为前缀散文本就 inert（不驱动行为，见 rule-placement-three-layers），且截断后半句烂尾、
#   老数据还漏 markdown，看着乱。name（kebab 主题名）本身就是"存在性"指针——模型据此认得
#   "我有这条"再用 recall 取详情（按名搜→认得）。详情归 recall，索引只给"在哪"。


def _build_context() -> str | None:
    """构建每轮注入前缀的记忆索引。

    两级：用户画像(user) → 全文注入；其余 → 仅列主题名（紧凑、无描述），详情靠 recall。
    """
    mem_dir = Path(MEMORY_DIR)
    d = mem_dir
    full_entries: list[str] = []      # user：全文注入
    topic_names: list[str] = []       # 其余：仅主题名（存在性指针）

    for f in sorted(d.iterdir()):
        if f.name == "MEMORY.md" or f.suffix != ".md":
            continue
        try:
            meta, body = _split_frontmatter(f.read_text(encoding="utf-8"))
            if not _is_active(meta):
                continue
            name = meta.get("name", f.stem)
            mem_type = meta.get("type", "")
            if mem_type == "user":
                # 用户画像：全文注入、不截断，保留换行结构便于模型读
                full_entries.append(f"  {name}:\n{body.strip()}")
            else:
                topic_names.append(name)
        except Exception:
            continue

    if not full_entries and not topic_names:
        return None

    lines = ["你拥有以下记忆（用 recall 按主题名取详情，不要在回复中逐字复述）："]
    if full_entries:
        lines.extend(full_entries)
        lines.append("")
    if topic_names:
        lines.append("  其它主题（recall 可取）：")
        lines.append("  " + " · ".join(sorted(topic_names)))

    return "\n".join(lines)


def get_context() -> str | None:
    """返回缓存的记忆索引，变化后自动重建（缓存保前缀稳定）。"""
    global _context_cache, _context_dirty
    if _context_dirty or _context_cache is None:
        _context_cache = _build_context()
        _context_dirty = False
    return _context_cache


def mark_dirty() -> None:
    """记忆变更后调用，下次 get_context 自动重建。"""
    global _context_dirty
    _context_dirty = True


def clear_dirty() -> None:
    """重建后清除脏标记。"""
    global _context_dirty
    _context_dirty = False


def is_dirty() -> bool:
    """检查记忆索引是否需要重建。"""
    return _context_dirty


TOOLS_MEMORY = [
    {
        "_meta": {"group": "core", "label": "记住", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "remember",
            "description": "记住知识，跨会话持久化。同名覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "记忆名（kebab-case），如 user-prefers-short",
                    },
                    "content": {"type": "string", "description": "记忆内容"},
                    "type": {
                        "type": "string",
                        "enum": ["user", "knowledge", "strategy", "reference"],
                        "description": "user/知识/策略/参考",
                    },
                    "fingerprint": {
                        "type": "string",
                        "description": (
                            "同类场景指纹标识（可选）。同指纹记忆自动合并到已有文件——"
                            "更新内容、递增 times_encountered。"
                            "如 improvement_loop、memory_corruption。"
                        ),
                    },
                    "contradicts": {
                        "type": "string",
                        "description": (
                            "声明此记忆替代/修正已有记忆的名称（可选）。"
                            "写入双方 frontmatter 形成双向标注。"
                            "如 user-prefers-short-v2 替代 user-prefers-short。"
                        ),
                    },
                },
                "required": ["name", "content", "type"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "回忆", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "recall",
            "description": "搜索记忆库，查找相关记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "忘记", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "forget",
            "description": "退役一条过时或错误记忆；默认移入可恢复归档，不物理销毁。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的记忆名称"},
                    "superseded_by": {
                        "type": "string",
                        "description": (
                            "被哪条记忆取代（可选）。传则软删除——保留文件标记 superseded，"
                            "后续 recall 自动跳过。不传则硬删除。"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "退役依据（推荐填写），用于后续审计和恢复判断。",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "记忆审计", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "memory_audit",
            "description": "审计活跃、冲突、过期、重复和已退役记忆；只报告候选，不自动删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stale_days": {
                        "type": "integer",
                        "description": "多久未更新视为待复核，默认 180 天。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "采用认知资产", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "adopt_asset",
            "description": (
                "明确采用本会话刚由 recall/rag_search 返回的资产。"
                "只有资产实质影响后续判断或动作时调用，并说明用途。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_kind": {
                        "type": "string",
                        "enum": ["memory", "knowledge"],
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "recall 返回的记忆名，或 rag_search 返回的 knowledge:path。",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "该资产具体改变了什么判断或后续动作。",
                    },
                },
                "required": ["asset_kind", "asset_id", "purpose"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "反馈认知资产", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "feedback_asset",
            "description": (
                "对已有任务结果的资产采用记录给出显式 helpful/misleading 反馈。"
                "不会从任务结果自动推断价值，也不会自动修改或删除资产。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_kind": {
                        "type": "string",
                        "enum": ["memory", "knowledge"],
                    },
                    "asset_id": {
                        "type": "string",
                        "description": "记忆名或 rag_search 返回的 source 路径。",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["helpful", "misleading"],
                    },
                    "reason": {
                        "type": "string",
                        "description": "资产具体帮助了什么，或在哪个判断上造成了误导。",
                    },
                    "adoption_id": {
                        "type": "string",
                        "description": "可选；采用事件 ID 或其唯一前缀，用于精确指定历史采用。",
                    },
                },
                "required": ["asset_kind", "asset_id", "verdict", "reason"],
            },
        },
    },
    {
        "_meta": {"group": "core", "label": "搜索会话", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "search_sessions",
            "description": (
                "搜索历史会话摘要，按话题匹配。"
                "当你需要了解之前聊过某个话题的进度、结论、产出文件时调用。"
                "返回 top_n 条匹配会话的摘要（含话题、关键决策、产出文件）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 '世界模型'、'记忆系统'、'TUI重构'",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回条数，默认 3",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _dir() -> Path:
    p = Path(MEMORY_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return _dir() / "MEMORY.md"


def _mem_path(name: str) -> Path:
    return _dir() / f"{name}.md"


def _read_index() -> list[str]:
    """读取 MEMORY.md，返回各行。"""
    ip = _index_path()
    if not ip.exists():
        return []
    return ip.read_text(encoding="utf-8").strip().split("\n")


def _write_index(lines: list[str]) -> None:
    _index_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rebuild_index() -> None:
    """从 memory/*.md 文件重建 MEMORY.md。"""
    entries: list[str] = []
    for f in sorted(_dir().glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
            if not _is_active(meta):
                continue
            name = meta.get("name", f.stem)
            desc = meta.get("description", "")
            entries.append(f"- [{name}]({f.name}) — {desc}")
        except Exception:
            continue
    _write_index(entries)


def _find_by_fingerprint(fp: str) -> Path | None:
    """扫描 memory/ 找 metadata.fingerprint 匹配的文件（仅匹配 metadata 下的
    fingerprint 字段，避免 description/body 中的偶然命中）。
    """
    for f in sorted(_dir().glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
            if _is_active(meta) and meta.get("fingerprint") == fp:
                return f
        except Exception:
            continue
    return None


def _merge_memory(existing_path: Path, name: str, content: str,
                  mem_type: str, fingerprint: str, now: str) -> str:
    """合并到已有记忆：更新内容、递增计数器、刷新时间。保留原文件名。"""
    meta, _old_body = _split_frontmatter(existing_path.read_text(encoding="utf-8"))
    old_name = meta.get("name", existing_path.stem)
    times = int(meta.get("times_encountered", 1)) + 1
    revision = int(meta.get("revision", 1)) + 1
    created = meta.get("created", meta.get("updated", now))

    first_sentence = content.split("。")[0].split("\n")[0].strip()
    desc = first_sentence[:30] if first_sentence else content[:30]

    text = (
        f"---\n"
        f"name: {old_name}\n"
        f"description: {desc}\n"
        f"metadata:\n"
        f"  type: {mem_type}\n"
        f"  status: active\n"
        f"  created: {created}\n"
        f"  updated: {now}\n"
        f"  revision: {revision}\n"
        f"  fingerprint: {fingerprint}\n"
        f"  times_encountered: {times}\n"
        f"  last_encountered: {now}\n"
        f"---\n\n"
        f"{content}\n"
    )
    existing_path.write_text(text, encoding="utf-8")
    _rebuild_index()
    mark_dirty()
    return f"已合并到已有记忆: {old_name}（第 {times} 次遇到）"


def _remember(name: str, content: str, mem_type: str,
              fingerprint: str | None = None,
              contradicts: str | None = None) -> str:
    """创建或更新一条记忆。有 fingerprint 时先扫描合并，避免同质散成 N 条。

    contradicts: 声明此记忆替代/修正另一条记忆。写入双方 frontmatter 形成
    双向标注——本条写 contradicts，对方写 contradicted_by。
    """
    if contradicts == name:
        return f"不能让记忆 {name} 替代自身；请提供另一条旧记忆名称。"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── fingerprint 合并：同指纹先查已有，找到即合并 ──
    if fingerprint:
        existing = _find_by_fingerprint(fingerprint)
        if existing:
            return _merge_memory(existing, name, content, mem_type, fingerprint, now)

    # ── LLM 蒸馏：提取 3-5 条结构化断言（失败不阻塞写入）──
    distilled: list[str] = []
    if len(content) >= 80:
        with contextlib.suppress(Exception):
            distilled = _distill(content)

    # ── 正常新建（或 name 覆盖）──
    target = _mem_path(name)
    old_meta: dict[str, str] = {}
    if target.exists():
        old_meta, _ = _split_frontmatter(target.read_text(encoding="utf-8"))
    created = old_meta.get("created", old_meta.get("updated", now))
    revision = int(old_meta.get("revision", 0)) + 1
    fingerprint = fingerprint or old_meta.get("fingerprint") or None
    first_sentence = content.split("。")[0].split("\n")[0].strip()
    desc = first_sentence[:30] if first_sentence else content[:30]

    meta_lines = [
        f"name: {name}",
        f"description: {desc}",
        "metadata:",
        f"  type: {mem_type}",
        "  status: active",
        f"  created: {created}",
        f"  updated: {now}",
        f"  revision: {revision}",
    ]
    if fingerprint:
        meta_lines.extend([
            f"  fingerprint: {fingerprint}",
            "  times_encountered: 1",
            f"  last_encountered: {now}",
        ])
    if contradicts:
        meta_lines.append(f"  contradicts: {contradicts}")
    for assertion in distilled:
        meta_lines.append(f"  distilled: {assertion}")

    text = "---\n" + "\n".join(meta_lines) + f"\n---\n\n{content}\n"
    target.write_text(text, encoding="utf-8")

    # ── 矛盾双向标注：对方写 contradicted_by ──
    if contradicts:
        opp = _mem_path(contradicts)
        if opp.exists():
            raw = opp.read_text(encoding="utf-8")
            raw = _set_metadata_fields(
                raw,
                {
                    "contradicted_by": name,
                    "superseded_by": name,
                    "status": "superseded",
                    "updated": now,
                },
            )
            opp.write_text(raw, encoding="utf-8")

    _rebuild_index()
    mark_dirty()
    parts = [f"已记住: {name}"]
    if contradicts:
        parts.append(f"（标记为替代 {contradicts}）")
    return "".join(parts)


def _tokenize(text: str) -> set[str]:
    """分词:ASCII alnum 词 + 中文相邻 bigram(单字 CJK 退化为单字)。

    bigram 让"分析论文"与"论文分析"互相命中(换序/换说法),无需分词词典。
    """
    text = text.lower()
    tokens: set[str] = set(_TOKEN_ASCII.findall(text))
    for run in _TOKEN_CJK.findall(text):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度: |A∩B| / |A∪B|。两集合均为空返回 0.0。"""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tokenize_for_similarity(text: str) -> set[str]:
    """字符级分词用于相似度检测——比 bigram 更粗粒度，适合检测近重复。

    中文按单字切，ASCII 仍按 alnum 词——避免 bigram 把中文切太碎导致 Jaccard 过低。
    """
    text = text.lower()
    tokens: set[str] = set(_TOKEN_ASCII.findall(text))
    tokens.update(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    return tokens


_DISTILL_PROMPT = """Extract 3-5 atomic assertions from the agent memory text below.
Each assertion must be one short sentence. Focus on: user preferences, project
constraints, proven strategies, known failure modes, key decisions. If the text
is purely administrative (e.g., "task completed"), return an empty JSON list.

Output ONLY a JSON array of strings. No preamble, no markdown.

Memory text:
{content}"""


def _distill(content: str) -> list[str]:
    """Flash 提取 3-5 条结构化断言。延迟导入避免循环依赖。"""
    import json as _json

    from ..llm import AVAILABLE_MODELS as _MODELS

    backend = _MODELS["flash"]
    prompt = _DISTILL_PROMPT.format(content=content[:2000])
    text, _ = backend.chat_non_stream(
        [{"role": "user", "content": prompt}],
        on_token=lambda _t: None,
        max_tokens=300,
    )
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        result = _json.loads(text[start:end + 1])
        return [str(r).strip() for r in result if str(r).strip()]
    except (_json.JSONDecodeError, TypeError):
        return []


def _score_memory(q_tokens: set[str], q_lower: str,
                  name: str, desc: str, body: str,
                  updated: str = "",
                  distilled: list[str] | None = None) -> float:
    """给一条记忆打分:每个 token 取命中的最高权重字段累加 + 精确子串加成 + 时间衰减。

    只对语义字段(name/description/body/distilled)打分,绝不碰 frontmatter 结构词
    (metadata/type/updated…),否则 'ad' 会误命中 'met·ad·ata'、'type' 命中所有记忆。

    distilled 断言权重高于 body——它们是浓缩的事实，命中信号更强。
    """
    fields = [(name.lower(), _PARAMS.weight_name),
              (desc.lower(), _PARAMS.weight_desc),
              (body.lower(), _PARAMS.weight_body)]
    if distilled:
        for d in distilled:
            fields.append((d.lower(), _PARAMS.weight_desc))  # distilled 权重=desc
    score = 0.0
    for tok in q_tokens:
        best = 0.0
        for field_text, weight in fields:
            if weight > best and tok in field_text:
                best = weight
        score += best
    if q_lower and any(q_lower in field_text for field_text, _w in fields):
        score += _PARAMS.exact_bonus

    # ── 时间衰减：旧记忆自动降权 ──
    dr = _PARAMS.decay_rate
    if dr > 0.0 and updated:
        try:
            ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age_days = (datetime.now(UTC) - ts).total_seconds() / 86400.0
            if age_days > 0:
                score *= 1.0 / (1.0 + age_days * dr)
        except (ValueError, OSError):
            pass  # 无法解析时间 → 不衰减

    return score


def _scan_into(directory: Path, q_tokens: set[str], q_lower: str,
               default_type: str, skip_index: bool,
               scored: list[tuple[float, str, str, str, str, str]]) -> None:
    """给一个目录下的 *.md 打分,命中的累加进 scored（含 body 用于后续相似度检测）。

    archive 文件无 frontmatter → name=文件名、type 取 default_type,照样可召回。
    """
    for f in sorted(directory.glob("*.md")):
        if skip_index and f.name == "MEMORY.md":
            continue
        try:
            full = f.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _split_frontmatter(full)
        # 跳过已 superseded 的记忆
        if meta.get("superseded_by"):
            continue
        distilled = _extract_distilled(full) or None
        s = _score_memory(q_tokens, q_lower, meta.get("name", f.stem),
                          meta.get("description", ""), body,
                          meta.get("updated", ""),
                          distilled=distilled)
        if s <= _PARAMS.recall_min_score:
            continue
        snippet = body[:_SNIPPET_CHARS].replace("\n", " ")
        scored.append((s, meta.get("updated", ""), f.stem,
                       meta.get("type", "") or default_type, snippet, body))


def _recall(query: str) -> str:
    """搜索记忆 + 任务归档:分词加权打分 → 相关度排序 → top-K(换说法也能命中)。

    跨 memory/(个人增量)与 tasks/archive/(过往任务的架构教训)两库——教训多写在
    归档里,只索引 memory 会让它们对检索成"只写不读的坟场"(agent 曾因此重造已有机制)。
    """
    q_lower = query.lower().strip()
    # 双向扩词：中↔英互搜
    expanded = q_lower
    for cn, en_list in _TRANSLITERATE.items():
        if cn in q_lower:
            expanded += " " + " ".join(en_list)
    q_tokens = _tokenize(expanded)
    scored: list[tuple[float, str, str, str, str]] = []  # (score, updated, name, type, snippet)
    _scan_into(_dir(), q_tokens, q_lower, "", True, scored)
    archive = Path(ARCHIVE_DIR)
    if archive.is_dir():
        _scan_into(archive, q_tokens, q_lower, "task", False, scored)

    if not scored:
        return f"未找到与「{query}」相关的记忆。"

    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)  # 分数高优先,平手按时间近因
    top = scored[:_PARAMS.recall_top_k]

    # ── 相似度检测 + 合并：高重叠条目不再各自 raw dump ──
    _sim_threshold = 0.35
    # merged_into[name] = primary  — 此条目被合并到 primary
    merged_into: dict[str, str] = {}
    token_map: dict[str, set[str]] = {}
    for _s, _u, name, _mt, _sn, body in top:
        token_map[name] = _tokenize_for_similarity(body)

    for i in range(len(top)):
        name_i = top[i][2]
        if name_i in merged_into:
            continue
        for j in range(i + 1, len(top)):
            name_j = top[j][2]
            if name_j in merged_into:
                continue
            sim = _jaccard(token_map[name_i], token_map[name_j])
            if sim >= _sim_threshold:
                # i 排位更高 → j 合并到 i
                merged_into[name_j] = name_i

    # ── 构建输出 ──
    lines = [f"找到 {len(scored)} 条相关记忆（按相关度，显示前 {len(top)}）：\n"]
    for _s, _u, name, mtype, snippet, _body in top:
        if name in merged_into:
            continue  # 被合并到别的条目，跳过
        tag = f"[{mtype}]" if mtype else ""
        # 收集合并到此条目的其他名称
        siblings = [n for n, p in merged_into.items() if p == name]
        if siblings:
            merged_note = "（含: " + ", ".join(siblings) + " — 内容重叠已合并）"
            lines.append(f"  {name} {merged_note} {tag}")
        else:
            lines.append(f"  {name} {tag}")
        lines.append(f"    {snippet}")
        lines.append("")

    with contextlib.suppress(Exception):
        from ..asset_usage import record_retrieval

        record_retrieval("memory", [row[2] for row in top], query)
    lines.append("若其中某条实质影响后续判断或动作，请调用 adopt_asset 明确记录采用及用途。")
    return "\n".join(lines).strip()


def _forget(
    name: str,
    superseded_by: str | None = None,
    reason: str | None = None,
) -> str:
    """Retire one memory without irreversible deletion.

    superseded_by: 不真正删除文件，改为标记 superseded 状态（软删除），
    后续 recall 自动跳过。不传则移入 memory/_retired/ 可恢复归档。
    """
    fp = _mem_path(name)
    if not fp.exists():
        return f"记忆不存在: {name}"

    if superseded_by:
        raw = fp.read_text(encoding="utf-8")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        fields = {
            "superseded_by": superseded_by,
            "status": "superseded",
            "updated": now,
        }
        if reason:
            fields["retired_reason"] = reason
        raw = _set_metadata_fields(raw, fields)
        fp.write_text(raw, encoding="utf-8")
        _rebuild_index()
        mark_dirty()
        return f"已软删除: {name}（被 {superseded_by} 取代）"

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = _set_metadata_fields(
        fp.read_text(encoding="utf-8"),
        {
            "status": "retired",
            "retired_at": now,
            "retired_reason": reason or "未提供",
            "updated": now,
        },
    )
    retired_dir = _dir() / _RETIRED_DIR_NAME
    retired_dir.mkdir(exist_ok=True)
    destination = retired_dir / fp.name
    if destination.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = retired_dir / f"{fp.stem}-{stamp}{fp.suffix}"
    fp.write_text(raw, encoding="utf-8")
    fp.replace(destination)
    _rebuild_index()
    mark_dirty()
    return f"已忘记: {name}（已移入可恢复归档 {_RETIRED_DIR_NAME}/）"


def _memory_audit(stale_days: int = 180) -> str:
    """Report lifecycle candidates; never mutates or deletes memories."""
    stale_days = max(1, min(int(stale_days), 3650))
    now = datetime.now(UTC)
    active: list[str] = []
    superseded: list[str] = []
    conflicts: list[str] = []
    stale: list[str] = []
    fingerprints: dict[str, list[str]] = {}
    for path in sorted(_dir().glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        try:
            meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        name = meta.get("name", path.stem)
        if not _is_active(meta):
            superseded.append(name)
            continue
        active.append(name)
        if meta.get("contradicted_by"):
            conflicts.append(f"{name} → {meta['contradicted_by']}")
        fingerprint = meta.get("fingerprint")
        if fingerprint:
            fingerprints.setdefault(fingerprint, []).append(name)
        updated = meta.get("updated")
        if not updated:
            stale.append(f"{name}（无更新时间）")
            continue
        try:
            changed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            stale.append(f"{name}（时间不可解析）")
            continue
        if (now - changed).days >= stale_days:
            stale.append(f"{name}（{(now - changed).days} 天）")

    duplicates = [
        f"{fingerprint}: {', '.join(names)}"
        for fingerprint, names in fingerprints.items()
        if len(names) > 1
    ]
    retired_dir = _dir() / _RETIRED_DIR_NAME
    retired_count = len(list(retired_dir.glob("*.md"))) if retired_dir.is_dir() else 0
    lines = [
        "记忆生命周期审计",
        f"- 活跃: {len(active)}",
        f"- 已取代/停用: {len(superseded)}",
        f"- 可恢复归档: {retired_count}",
        f"- 待复核陈旧项（≥{stale_days}天）: {len(stale)}",
        f"- 未解决冲突: {len(conflicts)}",
        f"- 重复 fingerprint: {len(duplicates)}",
    ]
    for label, items in (
        ("陈旧候选", stale),
        ("冲突候选", conflicts),
        ("重复候选", duplicates),
    ):
        if items:
            lines.append(f"\n{label}:")
            lines.extend(f"- {item}" for item in items[:20])
    lines.append("\n审计只报告候选；更新用 remember，同类替代用 contradicts，退役用 forget。")
    with contextlib.suppress(Exception):
        from ..asset_usage import format_usage_summary

        lines.append(format_usage_summary("memory"))
    return "\n".join(lines)


def _search_sessions(query: str, top_n: int = 3) -> str:
    """搜索历史会话摘要。延迟导入避免循环依赖。"""
    from ..session_summary import search_sessions as _ss
    results = _ss(query, top_n=top_n)
    if not results:
        return f"未找到与「{query}」相关的历史会话。"
    lines = [f"找到 {len(results)} 条与「{query}」相关的历史会话：\n"]
    for i, r in enumerate(results, 1):
        sid = r["session_id"]
        topics = r.get("topics", "（未识别）")
        text = r.get("text", "")
        if len(text) > 300:
            text = text[:300] + "..."
        lines.append(f"## [{i}] {sid}")
        lines.append(f"  话题: {topics}")
        lines.append(f"  摘要: {text}")
        if r.get("unfinished"):
            lines.append(f"  未竟事项: {r['unfinished']}")
        lines.append(f"  （完整摘要含产出文件: read_file {r['path']}）")
        lines.append("")
    return "\n".join(lines)


def execute(name: str, args: dict) -> str | None:
    if name == "remember":
        return _remember(args["name"], args["content"], args["type"],
                         args.get("fingerprint"), args.get("contradicts"))
    if name == "recall":
        return _recall(args["query"])
    if name == "forget":
        return _forget(args["name"], args.get("superseded_by"), args.get("reason"))
    if name == "memory_audit":
        return _memory_audit(int(args.get("stale_days", 180)))
    if name == "adopt_asset":
        from ..asset_usage import adopt_asset

        return adopt_asset(args["asset_kind"], args["asset_id"], args["purpose"])
    if name == "feedback_asset":
        from ..asset_usage import feedback_asset

        return feedback_asset(
            args["asset_kind"],
            args["asset_id"],
            args["verdict"],
            args["reason"],
            args.get("adoption_id", ""),
        )
    if name == "search_sessions":
        return _search_sessions(args["query"], int(args.get("top_n", 3)))
    return None
