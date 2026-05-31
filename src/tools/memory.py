"""记忆系统：agent 可以 remember / recall / forget 跨会话知识。"""

from datetime import UTC, datetime
from pathlib import Path

from ..config import MEMORY_DIR

# ── 记忆索引缓存（避免每次请求重复读文件） ──
_context_cache: str | None = None
_context_dirty: bool = True


def _build_context() -> str | None:
    """从文件构建记忆索引字符串。"""
    mem_dir = Path(MEMORY_DIR)
    index_file = mem_dir / "MEMORY.md"
    if not index_file.exists():
        return None
    d = mem_dir
    entries: list[str] = []
    for f in sorted(d.iterdir()):
        if f.name == "MEMORY.md" or not f.suffix == ".md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
            name, desc = f.stem, ""
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    for line in text[3:end].split("\n"):
                        line = line.strip()
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip().rstrip(".")
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
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "记住一条知识，跨会话持久化。当你学到用户的偏好、发现有效的方法、"
                "或者想记录重要信息时调用。同名记忆会覆盖更新。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记忆名称（kebab-case），如 user-prefers-short-answers"},
                    "content": {"type": "string", "description": "记忆内容，写清楚事实、原因和适用场景"},
                    "type": {
                        "type": "string",
                        "enum": ["user", "knowledge", "strategy", "reference"],
                        "description": "类型：user=用户偏好, knowledge=学到的知识, strategy=有效方法, reference=外部资源",
                    },
                },
                "required": ["name", "content", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "搜索记忆库，查找相关的记忆。在接到任务时先调用，了解用户偏好和已知方法。",
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
            text = f.read_text(encoding="utf-8")
            # 从 frontmatter 提取 name 和 description
            name = f.stem
            desc = ""
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    for line in text[3:end].split("\n"):
                        line = line.strip()
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                        elif line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip()
            # 获取第一段正文作摘要
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


def _recall(query: str) -> str:
    """搜索记忆，返回匹配结果。"""
    d = _dir()
    results: list[tuple[str, str, str]] = []  # (name, type, snippet)
    q = query.lower()

    for f in sorted(d.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(encoding="utf-8").lower()
        except Exception:
            continue
        if q not in text:
            continue

        # 读取 frontmatter 获取元信息
        full = f.read_text(encoding="utf-8")
        mem_type = ""
        body = full
        if full.startswith("---"):
            end = full.find("---", 3)
            if end != -1:
                for line in full[3:end].split("\n"):
                    if line.strip().startswith("type:"):
                        mem_type = line.split(":", 1)[1].strip()
                body = full[end + 3:].strip()

        snippet = body[:200].replace("\n", " ")
        results.append((f.stem, mem_type, snippet))

    if not results:
        return f"未找到与「{query}」相关的记忆。"

    lines = [f"找到 {len(results)} 条相关记忆：\n"]
    for name, mtype, snippet in results:
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
