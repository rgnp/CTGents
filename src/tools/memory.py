"""记忆系统：agent 可以 remember / recall / forget 跨会话知识。"""

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

# 注：曾有正则探测"该考虑记"时刻的 detect_signal——已整链删除。
# 判断"该记什么"是语义问题，正则做不了（"不对，有 bug"和"你推荐 embedding
# 是错的"在正则眼里都是"不对"）。写入由 agent 自主判断，见 AGENTS.md「记忆边界」。
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
        "_meta": {"label": "记住", "dedup_blacklist": True},
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
                            "同类错误指纹标识（可选）。同指纹记忆自动合并到已有文件——"
                            "更新内容、递增 times_encountered。如 tool_arg_error。"
                        ),
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
        "_meta": {"label": "忘记", "dedup_blacklist": True},
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


def _find_by_fingerprint(fp: str) -> Path | None:
    """扫描 memory/ 找 metadata.fingerprint 匹配的文件，返回 Path 或 None。

    仅匹配 metadata 下的 fingerprint 字段，避免 description/body 中的偶然命中。
    多个匹配（理论上不应发生）返回第一个。
    """
    for f in sorted(_dir().glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, _ = _split_frontmatter(f.read_text(encoding="utf-8"))
            if meta.get("fingerprint") == fp:
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

    first_sentence = content.split("。")[0].split("\n")[0].strip()
    desc = first_sentence[:30] if first_sentence else content[:30]

    text = (
        f"---\n"
        f"name: {old_name}\n"
        f"description: {desc}\n"
        f"metadata:\n"
        f"  type: {mem_type}\n"
        f"  updated: {now}\n"
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
              fingerprint: str | None = None) -> str:
    """创建或更新一条记忆。有 fingerprint 时先扫描合并，避免同质散成 N 条。"""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── fingerprint 合并：同指纹先查已有，找到即合并 ──
    if fingerprint:
        existing = _find_by_fingerprint(fingerprint)
        if existing:
            return _merge_memory(existing, name, content, mem_type, fingerprint, now)

    # ── 正常新建（或 name 覆盖）──
    first_sentence = content.split("。")[0].split("\n")[0].strip()
    desc = first_sentence[:30] if first_sentence else content[:30]

    meta_lines = [
        f"name: {name}",
        f"description: {desc}",
        "metadata:",
        f"  type: {mem_type}",
        f"  updated: {now}",
    ]
    if fingerprint:
        meta_lines.extend([
            f"  fingerprint: {fingerprint}",
            "  times_encountered: 1",
            f"  last_encountered: {now}",
        ])

    text = "---\n" + "\n".join(meta_lines) + f"\n---\n\n{content}\n"
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


def _scan_into(directory: Path, q_tokens: set[str], q_lower: str,
               default_type: str, skip_index: bool,
               scored: list[tuple[float, str, str, str, str]]) -> None:
    """给一个目录下的 *.md 打分,命中的累加进 scored。archive 文件无 frontmatter
    → name=文件名、type 取 default_type,照样可召回(治"只写不读的坟场")。
    """
    for f in sorted(directory.glob("*.md")):
        if skip_index and f.name == "MEMORY.md":
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
        scored.append((s, meta.get("updated", ""), f.stem,
                       meta.get("type", "") or default_type, snippet))


def _recall(query: str) -> str:
    """搜索记忆 + 任务归档:分词加权打分 → 相关度排序 → top-K(换说法也能命中)。

    跨 memory/(个人增量)与 tasks/archive/(过往任务的架构教训)两库——教训多写在
    归档里,只索引 memory 会让它们对检索成"只写不读的坟场"(agent 曾因此重造已有机制)。
    """
    q_lower = query.lower().strip()
    q_tokens = _tokenize(query)
    scored: list[tuple[float, str, str, str, str]] = []  # (score, updated, name, type, snippet)
    _scan_into(_dir(), q_tokens, q_lower, "", True, scored)
    archive = Path(ARCHIVE_DIR)
    if archive.is_dir():
        _scan_into(archive, q_tokens, q_lower, "task", False, scored)

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
        return _remember(args["name"], args["content"], args["type"],
                         args.get("fingerprint"))
    if name == "recall":
        return _recall(args["query"])
    if name == "forget":
        return _forget(args["name"])
    return None
