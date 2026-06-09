"""记忆系统：agent 可以 remember / recall / forget 跨会话知识。"""

import re
from datetime import UTC, datetime
from pathlib import Path

from ..config import MEMORY_DIR
from ..params import MEMORY as _PARAMS

# recall 片段长度(结构性常量,留本模块)。
_SNIPPET_CHARS = 200
# 分词:ASCII alnum 词 + 中文连续块(下方切 bigram)。
_TOKEN_ASCII = re.compile(r"[a-z0-9]+")
_TOKEN_CJK = re.compile(r"[一-鿿]+")

# ── 记忆写入信号（机械探测"该考虑记"的时刻；写入仍由 agent 判断） ──
# 固定触发的错在于把"探测"和"写入"捆死。这里只机械探测语义事件，
# 命中时挂一行易失提示，记不记、记什么仍是 agent 自愿调 remember。
_SIGNAL_EXPLICIT = re.compile(r"记住|记一下|记下来|记下|帮我记|remember", re.I)
_SIGNAL_CORRECTION = re.compile(
    # 不对(?!劲)：摘掉"不对劲"——那是"感觉蹊跷"的成语，不是在纠正
    r"不对(?!劲)|不是这样|错了|搞错|应该是|其实是|并不是|别这样|不要这样|理解错|你弄错"
)
_SIGNAL_PREFERENCE = re.compile(
    r"我喜欢|我习惯|我倾向|我更喜欢|我偏好|我们项目|我们的约定|以后都|以后请|每次都要|默认要|默认用"
)

_NUDGE_EXPLICIT = "💡 用户要求记住某事。用 remember 存下来(name/content/type)，别只在本轮回应。"
_NUDGE_CORRECTION = "💡 用户纠正了你。若这是跨会话通用的偏好或教训，考虑 remember(type=user/strategy)。"
_NUDGE_PREFERENCE = "💡 用户表达了偏好/惯例。若跨会话通用且 AGENTS.md 未覆盖，考虑 remember(type=user)。"


def detect_signal(user_text: str) -> str | None:
    """探测最新 user 消息里"值得记忆"的语义信号，命中返回一行提示。

    只探测、不写入：写入仍是 agent 自愿调 remember。按强度优先匹配：
    显式要求 > 纠正 > 偏好陈述。无信号返回 None。
    """
    if not user_text:
        return None
    if _SIGNAL_EXPLICIT.search(user_text):
        return _NUDGE_EXPLICIT
    if _SIGNAL_CORRECTION.search(user_text):
        return _NUDGE_CORRECTION
    if _SIGNAL_PREFERENCE.search(user_text):
        return _NUDGE_PREFERENCE
    return None


# ── 记忆索引缓存（避免每次请求重复读文件） ──
_context_cache: str | None = None
_context_dirty: bool = True


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


def _build_context() -> str | None:
    """从文件构建记忆索引字符串。"""
    mem_dir = Path(MEMORY_DIR)
    index_file = mem_dir / "MEMORY.md"
    if not index_file.exists():
        return None
    d = mem_dir
    entries: list[str] = []
    for f in sorted(d.iterdir()):
        if f.name == "MEMORY.md" or f.suffix != ".md":
            continue
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
            name = meta.get("name", f.stem)
            desc = meta.get("description", "").rstrip(".")
            short = desc.split("。")[0].split("，")[0][:30] if desc else ""
            entries.append((name, short))
        except Exception:
            continue
    if not entries:
        return None
    lines = ["你拥有以下记忆（需要时用 recall 搜索详情，不要在回复中逐字复述）："]
    for name, short in entries:
        lines.append(f"  {name}: {short}")
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
        "_meta": {"label": "记住", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "remember",
            "description": "记住知识，跨会话持久化。同名覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记忆名（kebab-case），如 user-prefers-short"},
                    "content": {"type": "string", "description": "记忆内容"},
                    "type": {
                        "type": "string",
                        "enum": ["user", "knowledge", "strategy", "reference"],
                        "description": "user/知识/策略/参考",
                    },
                },
                "required": ["name", "content", "type"],
            },
        },
    },
    {
        "_meta": {"label": "回忆", "parallel_safe": True},
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
        "_meta": {"label": "忘记", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "forget",
            "description": "删除一条记忆。当记忆过时或错误时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的记忆名称"},
                },
                "required": ["name"],
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
            name = meta.get("name", f.stem)
            desc = meta.get("description", "")
            entries.append(f"- [{name}]({f.name}) — {desc}")
        except Exception:
            continue
    _write_index(entries)


def _remember(name: str, content: str, mem_type: str) -> str:
    """创建或更新一条记忆。"""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # description 只取第一句或前 30 字，避免索引过于冗长
    first_sentence = content.split("。")[0].split("\n")[0].strip()
    desc = first_sentence[:30] if first_sentence else content[:30]
    text = (
        f"---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        f"metadata:\n"
        f"  type: {mem_type}\n"
        f"  updated: {now}\n"
        f"---\n\n"
        f"{content}\n"
    )
    _mem_path(name).write_text(text, encoding="utf-8")
    _rebuild_index()
    mark_dirty()
    return f"已记住: {name}"


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


def _score_memory(q_tokens: set[str], q_lower: str,
                  name: str, desc: str, body: str) -> float:
    """给一条记忆打分:每个 token 取命中的最高权重字段累加 + 精确子串加成。

    只对语义字段(name/description/body)打分,绝不碰 frontmatter 结构词
    (metadata/type/updated…),否则 'ad' 会误命中 'met·ad·ata'、'type' 命中所有记忆。
    """
    fields = ((name.lower(), _PARAMS.weight_name),
              (desc.lower(), _PARAMS.weight_desc),
              (body.lower(), _PARAMS.weight_body))
    score = 0.0
    for tok in q_tokens:
        best = 0.0
        for field_text, weight in fields:
            if weight > best and tok in field_text:
                best = weight
        score += best
    if q_lower and any(q_lower in field_text for field_text, _w in fields):
        score += _PARAMS.exact_bonus
    return score


def _recall(query: str) -> str:
    """搜索记忆:分词加权打分 → 相关度排序 → top-K(换说法也能命中)。"""
    d = _dir()
    q_lower = query.lower().strip()
    q_tokens = _tokenize(query)
    scored: list[tuple[float, str, str, str, str]] = []  # (score, updated, name, type, snippet)

    for f in sorted(d.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            full = f.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _split_frontmatter(full)
        s = _score_memory(q_tokens, q_lower, meta.get("name", f.stem),
                          meta.get("description", ""), body)
        if s <= _PARAMS.recall_min_score:
            continue
        snippet = body[:_SNIPPET_CHARS].replace("\n", " ")
        scored.append((s, meta.get("updated", ""), f.stem, meta.get("type", ""), snippet))

    if not scored:
        return f"未找到与「{query}」相关的记忆。"

    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)  # 分数高优先,平手按时间近因
    top = scored[:_PARAMS.recall_top_k]
    lines = [f"找到 {len(scored)} 条相关记忆（按相关度，显示前 {len(top)}）：\n"]
    for _s, _u, name, mtype, snippet in top:
        tag = f"[{mtype}]" if mtype else ""
        lines.append(f"  {name} {tag}")
        lines.append(f"    {snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _forget(name: str) -> str:
    """删除一条记忆。"""
    fp = _mem_path(name)
    if not fp.exists():
        return f"记忆不存在: {name}"
    fp.unlink()
    _rebuild_index()
    mark_dirty()
    return f"已忘记: {name}"


def execute(name: str, args: dict) -> str | None:
    if name == "remember":
        return _remember(args["name"], args["content"], args["type"])
    if name == "recall":
        return _recall(args["query"])
    if name == "forget":
        return _forget(args["name"])
    return None
