"""研究知识库 — 基于 SQLite 的结构化科研知识管理。

架构：
  papers         — 论文元数据（标题、作者、摘要、年份、引用数）
  notes          — 研究笔记（可关联论文或独立存在）
  topics         — 层级化主题分类
  paper_topics   — 论文 ↔ 主题 多对多
  paper_relations— 论文间关系（引用、基于、矛盾、对比）
  reading_log    — 阅读记录

全部存储在 ~/.ctgents/research/kb.sqlite
"""

import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── 路径 ──
RESEARCH_DIR = Path.home() / ".ctgents" / "research"
KB_PATH = RESEARCH_DIR / "kb.sqlite"
_INDEX_VERSION_FILE = RESEARCH_DIR / ".rag_version"  # 跟踪是否需要重建索引


def _auto_reindex():
    """如果知识库有新内容，自动增量更新 RAG 研究索引。"""
    try:
        _ensure_dirs()
        # 检查文档数是否变化
        db = _get_db()
        paper_count = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        note_count = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        db.close()
        total = paper_count + note_count
        if total == 0:
            return

        last_count = 0
        if _INDEX_VERSION_FILE.exists():
            try:
                last_count = int(_INDEX_VERSION_FILE.read_text().strip())
            except Exception:
                pass

        if total != last_count:
            from .rag import index_research_content
            index_research_content()
            _INDEX_VERSION_FILE.write_text(str(total))
    except Exception:
        pass  # 静默失败，不影响主流程


def _get_db() -> sqlite3.Connection:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(KB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db():
    db = _get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,          -- arXiv ID 或 Semantic Scholar ID
            title TEXT NOT NULL,
            authors TEXT,                  -- JSON array
            abstract TEXT,
            year INTEGER,
            venue TEXT,
            citations INTEGER DEFAULT 0,
            references_count INTEGER DEFAULT 0,
            source TEXT,                   -- 'arxiv' | 'semantic_scholar' | 'manual'
            url TEXT,
            tldr TEXT,                     -- AI-generated one-liner
            added_at TEXT DEFAULT (datetime('now')),
            last_read TEXT
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_topics (
            paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            topic_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
            PRIMARY KEY (paper_id, topic_id)
        );

        CREATE TABLE IF NOT EXISTS note_topics (
            note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
            topic_id INTEGER REFERENCES topics(id) ON DELETE CASCADE,
            PRIMARY KEY (note_id, topic_id)
        );

        CREATE TABLE IF NOT EXISTS paper_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            target_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL,   -- 'cites' | 'builds_on' | 'contradicts' | 'compares' | 'extends'
            note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, relation_type)
        );

        CREATE TABLE IF NOT EXISTS reading_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
            action TEXT,                   -- 'searched' | 'read' | 'noted' | 'cited'
            read_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
        CREATE INDEX IF NOT EXISTS idx_papers_source ON papers(source);
        CREATE INDEX IF NOT EXISTS idx_notes_paper ON notes(paper_id);
        CREATE INDEX IF NOT EXISTS idx_paper_topics_topic ON paper_topics(topic_id);
        CREATE INDEX IF NOT EXISTS idx_reading_log_paper ON reading_log(paper_id);
    """)
    db.commit()
    db.close()


# 启动时初始化
_init_db()


# ═══════════════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════════════

TOOLS_RESEARCH: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "搜索学术论文（arXiv + Semantic Scholar），结果自动存入知识库。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，英文效果更好"},
                    "source": {"type": "string", "enum": ["arxiv", "semantic_scholar", "both"],
                               "description": "搜索源，默认 both"},
                    "max_results": {"type": "integer", "description": "最多返回条数，默认 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_paper",
            "description": "获取论文详情并记录阅读。支持 arXiv ID、URL 或知识库中的 paper_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "description": "arXiv ID（如 1706.03762）或 URL"},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "保存研究笔记到知识库。可关联论文、指定主题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "笔记内容"},
                    "title": {"type": "string", "description": "标题，不传则自动生成"},
                    "paper_id": {"type": "string", "description": "关联的论文 ID（可选）"},
                    "topics": {"type": "array", "items": {"type": "string"},
                               "description": "主题列表（可选，如 ['RL', 'attention']）"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "搜索知识库：论文、笔记、主题。支持关键词和筛选。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "scope": {"type": "string", "enum": ["all", "papers", "notes"],
                              "description": "搜索范围，默认 all"},
                    "topic": {"type": "string", "description": "按主题筛选"},
                    "year_from": {"type": "integer", "description": "论文年份起始"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 10"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_topics",
            "description": "查看知识库主题分类树。了解已有知识结构。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_papers",
            "description": "建立论文间关系：引用、基于、矛盾、对比、扩展。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "源论文 ID"},
                    "target_id": {"type": "string", "description": "目标论文 ID"},
                    "relation": {"type": "string",
                                 "enum": ["cites", "builds_on", "contradicts", "compares", "extends"],
                                 "description": "关系类型"},
                    "note": {"type": "string", "description": "关系说明（可选）"},
                },
                "required": ["source_id", "target_id", "relation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_stats",
            "description": "知识库统计：论文数、笔记数、主题数、阅读进度。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# 论文搜索
# ═══════════════════════════════════════════════════════════════

def _fetch_json(url: str, timeout: int = 10) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "ResearchAgent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _search_arxiv(query: str, max_results: int = 10) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = (f"http://export.arxiv.org/api/query?"
           f"search_query=all:{encoded}&start=0&max_results={max_results}"
           f"&sortBy=relevance&sortOrder=descending")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ResearchAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except Exception:
        return []

    import xml.etree.ElementTree as ET
    A = "{http://www.w3.org/2005/Atom}"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    results = []
    for entry in root.findall(f"{A}entry"):
        title = (entry.findtext(f"{A}title") or "").strip().replace("\n", " ")
        summary = (entry.findtext(f"{A}summary") or "").strip().replace("\n", " ")[:500]
        arxiv_id = (entry.findtext(f"{A}id") or "").split("/abs/")[-1]
        published = (entry.findtext(f"{A}published") or "")[:10]
        authors = [a.findtext(f"{A}name") or "" for a in entry.findall(f"{A}author")]
        results.append({
            "id": arxiv_id, "title": title, "abstract": summary,
            "authors": authors, "year": int(published[:4]) if published[:4].isdigit() else None,
            "source": "arxiv", "url": f"https://arxiv.org/abs/{arxiv_id}",
            "citations": 0, "references_count": 0,
        })
    return results


def _search_semantic_scholar(query: str, max_results: int = 10) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = (f"https://api.semanticscholar.org/graph/v1/paper/search?"
           f"query={encoded}&limit={max_results}"
           f"&fields=title,abstract,authors,year,externalIds,url,citationCount,referenceCount")
    data = _fetch_json(url, timeout=10)
    if not data:
        return []
    results = []
    for p in data.get("data", []):
        results.append({
            "id": p.get("paperId", ""),
            "title": p.get("title", ""),
            "abstract": (p.get("abstract") or "")[:500],
            "authors": [a.get("name", "") for a in p.get("authors", [])],
            "year": p.get("year"),
            "source": "semantic_scholar",
            "url": p.get("url", ""),
            "citations": p.get("citationCount", 0),
            "references_count": p.get("referenceCount", 0),
            "arxiv_id": p.get("externalIds", {}).get("ArXiv", ""),
        })
    return results


def _upsert_paper(p: dict) -> bool:
    """将论文写入知识库，已存在则更新引用数。"""
    db = _get_db()
    pid = p["id"]
    existing = db.execute("SELECT id FROM papers WHERE id=?", (pid,)).fetchone()
    if existing:
        db.execute("UPDATE papers SET citations=?, references_count=? WHERE id=?",
                   (p.get("citations", 0), p.get("references_count", 0), pid))
    else:
        db.execute(
            "INSERT INTO papers(id,title,authors,abstract,year,source,url,citations,references_count) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, p["title"], json.dumps(p.get("authors", []), ensure_ascii=False),
             p.get("abstract", ""), p.get("year"), p.get("source", ""),
             p.get("url", ""), p.get("citations", 0), p.get("references_count", 0)),
        )
    db.commit()
    db.close()
    return not existing  # True = 新论文


def search_papers(query: str, source: str = "both", max_results: int = 10) -> str:
    """搜索论文，结果自动入库。"""
    all_results = []
    max_each = min(max_results, 15)

    if source in ("arxiv", "both"):
        all_results.extend(_search_arxiv(query, max_each))
    if source in ("semantic_scholar", "both"):
        all_results.extend(_search_semantic_scholar(query, max_each))

    # 标题去重
    seen = set()
    unique = []
    for r in all_results:
        key = r["title"].lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    if not unique:
        return f"未找到与「{query}」相关的论文。"

    # 入库（最多 15 篇）
    new_count = sum(1 for r in unique[:15] if _upsert_paper(r))

    lines = [f"搜索「{query}」找到 {len(unique)} 篇（{new_count} 篇新入知识库）：\n"]
    for i, r in enumerate(unique[:max_results], 1):
        authors = ", ".join(r.get("authors", [])[:3])
        if len(r.get("authors", [])) > 3:
            authors += " 等"
        y = r.get("year", "?")
        c = f" | 引用 {r['citations']}" if r.get("citations") else ""
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {authors} | {y} | {r['source']}{c}")
        lines.append(f"   {r['abstract'][:200]}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append(f"   ID: {r['id']}")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 论文详情
# ═══════════════════════════════════════════════════════════════

def read_paper(paper_id: str) -> str:
    """获取论文详情，优先从知识库读取。"""
    # 标准化 ID
    aid = paper_id.strip()
    for pfx in ("https://arxiv.org/abs/", "https://arxiv.org/pdf/", "http://arxiv.org/abs/"):
        if aid.startswith(pfx):
            aid = aid[len(pfx):]
    aid = aid.rstrip(".pdf")

    # 先从知识库查
    db = _get_db()
    paper = db.execute("SELECT * FROM papers WHERE id=? OR id LIKE ?",
                       (aid, f"%{aid}%")).fetchone()

    if paper:
        # 记录阅读
        db.execute("INSERT INTO reading_log(paper_id,action) VALUES(?,'read')", (paper["id"],))
        db.execute("UPDATE papers SET last_read=datetime('now') WHERE id=?", (paper["id"],))
        db.commit()
        db.close()
        return _format_paper(dict(paper))

    # 从 API 获取
    data = _fetch_json(
        f"https://api.semanticscholar.org/graph/v1/paper/ArXiv:{aid}?"
        f"fields=title,abstract,authors,year,venue,citationCount,referenceCount,url,tldr",
        timeout=10,
    )

    if data:
        p = {
            "id": aid, "title": data.get("title", ""),
            "abstract": data.get("abstract") or "",
            "authors": [a.get("name", "") for a in data.get("authors", [])],
            "year": data.get("year"), "venue": data.get("venue", ""),
            "citations": data.get("citationCount", 0),
            "references_count": data.get("referenceCount", 0),
            "source": "semantic_scholar",
            "url": data.get("url", f"https://arxiv.org/abs/{aid}"),
            "tldr": (data.get("tldr") or {}).get("text", ""),
        }
        _upsert_paper(p)
        db.execute("INSERT INTO reading_log(paper_id,action) VALUES(?,'read')", (aid,))
        db.commit()
        db.close()
        return _format_paper(p)

    db.close()
    # arXiv 回退
    return _read_paper_arxiv_fallback(aid)


def _format_paper(p: dict) -> str:
    authors = json.loads(p.get("authors", "[]")) if isinstance(p.get("authors"), str) else (p.get("authors") or [])
    lines = [
        f"## {p.get('title', '')}",
        "",
        f"**作者**: {', '.join(authors)}",
        f"**年份**: {p.get('year', '?')} | **来源**: {p.get('venue') or p.get('source', '')}",
        f"**引用**: {p.get('citations', 0)} 次 | **参考文献**: {p.get('references_count', 0)} 篇",
        f"**ID**: {p['id']} | **URL**: {p.get('url', '')}",
    ]
    if p.get("tldr"):
        lines.append(f"\n**TL;DR**: {p['tldr']}")
    if p.get("abstract"):
        lines.append(f"\n### 摘要\n{p['abstract']}")
    return "\n".join(lines)


def _read_paper_arxiv_fallback(aid: str) -> str:
    url = f"http://export.arxiv.org/api/query?id_list={aid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ResearchAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except Exception:
        return f"无法获取论文 {aid}"

    import xml.etree.ElementTree as ET
    A = "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(raw)
    entry = root.find(f"{A}entry")
    if entry is None:
        return f"未找到论文 {aid}"
    title = (entry.findtext(f"{A}title") or "").strip()
    summary = (entry.findtext(f"{A}summary") or "").strip()
    authors = [a.findtext(f"{A}name") or "" for a in entry.findall(f"{A}author")]
    published = (entry.findtext(f"{A}published") or "")[:10]
    year = int(published[:4]) if published[:4].isdigit() else None

    # 入库
    _upsert_paper({"id": aid, "title": title, "abstract": summary,
                   "authors": authors, "year": year, "source": "arxiv",
                   "url": f"https://arxiv.org/abs/{aid}",
                   "citations": 0, "references_count": 0})

    return (
        f"## {title}\n\n**作者**: {', '.join(authors)}\n"
        f"**发表**: {published}\n**arXiv**: [{aid}](https://arxiv.org/abs/{aid})\n\n"
        f"### 摘要\n{summary}"
    )


# ═══════════════════════════════════════════════════════════════
# 笔记
# ═══════════════════════════════════════════════════════════════

def save_note(content: str, title: str = "", paper_id: str = "",
              topics: list[str] | None = None) -> str:
    if not title:
        title = content[:60].replace("\n", " ")
    db = _get_db()

    # 检查论文是否存在，不存在则不关联
    if paper_id and not db.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
        paper_id = ""

    db.execute("INSERT INTO notes(title,content,paper_id) VALUES(?,?,?)",
               (title, content, paper_id or None))
    note_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 关联主题
    for t in (topics or []):
        tid = _ensure_topic(db, t)
        db.execute("INSERT OR IGNORE INTO note_topics(note_id,topic_id) VALUES(?,?)",
                   (note_id, tid))

    # 关联论文主题到笔记主题
    if paper_id:
        db.execute("INSERT INTO reading_log(paper_id,action) VALUES(?,'noted')", (paper_id,))

    db.commit()
    db.close()
    return f"笔记已保存 [{note_id}]: {title}"


# ═══════════════════════════════════════════════════════════════
# 知识库搜索
# ═══════════════════════════════════════════════════════════════

def search_knowledge(query: str = "", scope: str = "all", topic: str = "",
                     year_from: int | None = None, limit: int = 10) -> str:
    db = _get_db()
    parts = []

    # 搜索论文
    if scope in ("all", "papers"):
        sql = "SELECT p.* FROM papers p"
        params: list = []
        conditions = []

        if topic:
            sql += " JOIN paper_topics pt ON p.id=pt.paper_id JOIN topics t ON pt.topic_id=t.id"
            conditions.append("t.name=?")
            params.append(topic)

        if query:
            conditions.append("(p.title LIKE ? OR p.abstract LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])

        if year_from:
            conditions.append("p.year >= ?")
            params.append(year_from)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY p.citations DESC LIMIT ?"
        params.append(limit)

        papers = db.execute(sql, params).fetchall()
        if papers:
            parts.append(f"── 论文 ({len(papers)} 篇) ──")
            for p in papers:
                p = dict(p)
                authors = ", ".join((json.loads(p.get("authors", "[]")) if isinstance(p.get("authors"), str) else [])[:3])
                parts.append(f"  [{p['id']}] {p['title'][:80]}")
                parts.append(f"      {authors} | {p.get('year','?')} | 引用 {p.get('citations',0)}")

    # 搜索笔记
    if scope in ("all", "notes"):
        sql = "SELECT n.*, p.title as paper_title FROM notes n LEFT JOIN papers p ON n.paper_id=p.id"
        params = []
        conditions = []

        if topic:
            sql += " JOIN note_topics nt ON n.id=nt.note_id JOIN topics t ON nt.topic_id=t.id"
            conditions.append("t.name=?")
            params.append(topic)

        if query:
            conditions.append("(n.title LIKE ? OR n.content LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY n.updated_at DESC LIMIT ?"
        params.append(limit)

        notes = db.execute(sql, params).fetchall()
        if notes:
            parts.append(f"\n── 笔记 ({len(notes)} 条) ──")
            for n in notes:
                n = dict(n)
                paper_ref = f" 📄{n.get('paper_title','')[:30]}" if n.get("paper_title") else ""
                parts.append(f"  [{n['id']}] {n['title'][:80]}{paper_ref}")
                parts.append(f"      {n['content'][:120].replace(chr(10), ' ')}")

    db.close()
    if not parts:
        return "知识库中暂无匹配内容。用 search_papers 添加论文，用 save_note 记录笔记。"
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 主题
# ═══════════════════════════════════════════════════════════════

def _ensure_topic(db, name: str) -> int:
    row = db.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    db.execute("INSERT INTO topics(name) VALUES(?)", (name,))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def kb_topics() -> str:
    db = _get_db()
    topics = db.execute(
        "SELECT t.name, t.description, COUNT(pt.paper_id) as paper_count, "
        "COUNT(nt.note_id) as note_count "
        "FROM topics t "
        "LEFT JOIN paper_topics pt ON t.id=pt.topic_id "
        "LEFT JOIN note_topics nt ON t.id=nt.topic_id "
        "GROUP BY t.id ORDER BY paper_count DESC"
    ).fetchall()
    db.close()

    if not topics:
        return "知识库中暂无主题。论文和笔记被标记主题后会自动创建。"

    lines = [f"知识库主题（{len(topics)} 个）：\n"]
    for t in topics:
        lines.append(f"  {t['name']:<20} 📄{t['paper_count']}篇  📝{t['note_count']}条")
        if t["description"]:
            lines.append(f"    {t['description'][:80]}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 论文关系
# ═══════════════════════════════════════════════════════════════

def link_papers(source_id: str, target_id: str, relation: str, note: str = "") -> str:
    db = _get_db()
    # 确保两篇论文都在库中
    for pid in (source_id, target_id):
        if not db.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            db.close()
            return f"论文 {pid} 不在知识库中，请先用 search_papers 或 read_paper 添加。"
    try:
        db.execute(
            "INSERT INTO paper_relations(source_id,target_id,relation_type,note) VALUES(?,?,?,?)",
            (source_id, target_id, relation, note),
        )
        db.commit()
        db.close()
        return f"已建立关系: {source_id} --{relation}--> {target_id}"
    except sqlite3.IntegrityError:
        db.close()
        return "该关系已存在。"


def kb_stats() -> str:
    db = _get_db()
    papers = db.execute("SELECT COUNT(*) as c FROM papers").fetchone()["c"]
    notes = db.execute("SELECT COUNT(*) as c FROM notes").fetchone()["c"]
    topics = db.execute("SELECT COUNT(*) as c FROM topics").fetchone()["c"]
    relations = db.execute("SELECT COUNT(*) as c FROM paper_relations").fetchone()["c"]
    read = db.execute("SELECT COUNT(DISTINCT paper_id) as c FROM reading_log WHERE action='read'").fetchone()["c"]
    unread = max(0, papers - read)

    # 最近添加
    recent_papers = db.execute("SELECT title,year FROM papers ORDER BY added_at DESC LIMIT 3").fetchall()
    recent_notes = db.execute("SELECT title FROM notes ORDER BY created_at DESC LIMIT 3").fetchall()
    db.close()

    lines = [
        "═══════════════════════════════",
        "       研究知识库状态",
        "═══════════════════════════════",
        "",
        f"  📄 论文: {papers} 篇（已读 {read}，未读 {unread}）",
        f"  📝 笔记: {notes} 条",
        f"  🏷️  主题: {topics} 个",
        f"  🔗 关系: {relations} 条",
    ]
    if recent_papers:
        lines.append("\n── 最近论文 ──")
        for r in recent_papers:
            lines.append(f"  {r['title'][:70]} ({r.get('year','?')})")
    if recent_notes:
        lines.append("\n── 最近笔记 ──")
        for r in recent_notes:
            lines.append(f"  {r['title'][:70]}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 调度
# ═══════════════════════════════════════════════════════════════

def execute(name: str, args: dict) -> str | None:
    result = None
    if name == "search_papers":
        result = search_papers(args.get("query", ""), args.get("source", "both"),
                               args.get("max_results", 10))
    elif name == "read_paper":
        result = read_paper(args.get("paper_id", ""))
    elif name == "save_note":
        result = save_note(args.get("content", ""), args.get("title", ""),
                          args.get("paper_id", ""), args.get("topics"))
    elif name == "search_knowledge":
        return search_knowledge(args.get("query", ""), args.get("scope", "all"),
                               args.get("topic", ""), args.get("year_from"),
                               args.get("limit", 10))
    elif name == "kb_topics":
        return kb_topics()
    elif name == "link_papers":
        return link_papers(args.get("source_id", ""), args.get("target_id", ""),
                          args.get("relation", ""), args.get("note", ""))
    elif name == "kb_stats":
        return kb_stats()

    # 写入操作后自动更新 RAG 索引
    if name in ("search_papers", "read_paper", "save_note"):
        _auto_reindex()

    return result
    return None
