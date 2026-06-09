"""科研工具集：论文扫描、批量读取、评分归档。

工具:
  scan_papers  — 搜索+提取arxiv ID+去重
  read_papers  — 批量读取arxiv摘要
  paper_grid   — 按领域生成评分总表
  analyze_paper / cross_validate / save_paper_card — 论文分析管线
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

_ARXIV_API = "http://export.arxiv.org/api/query"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_KNOWLEDGE_TOOLS = _PROJECT_ROOT / "knowledge" / "trajectory-prediction" / "tools"
_KNOWLEDGE_PAPERS = _PROJECT_ROOT / "knowledge" / "trajectory-prediction" / "papers"
_KNOWLEDGE_AD2026 = _PROJECT_ROOT / "knowledge" / "autonomous-driving-2026"

if str(_KNOWLEDGE_TOOLS) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_TOOLS))

# ── 工具定义 ──────────────────────────────────────────────

TOOLS_RESEARCH = [
    {
        "_meta": {"label": "分析论文", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "analyze_paper",
            "description": "分析论文：方法论分类（6种类型）+ 已知Gap匹配 + 生成论文卡片模板。输入标题和摘要即可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "论文标题"},
                    "abstract": {"type": "string", "description": "论文摘要"},
                    "contributions": {"type": "string", "description": "核心贡献（可选）"},
                    "authors": {"type": "string", "description": "作者（可选）"},
                    "conference": {"type": "string", "description": "会议/期刊（可选）"},
                    "year": {"type": "string", "description": "年份（可选）"},
                },
                "required": ["title", "abstract"],
            },
        },
    },
    {
        "_meta": {"label": "交叉验证", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "cross_validate",
            "description": "新论文与知识库已有论文交叉验证：矛盾检测、互补分析、Gap影响、方法论对比。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "论文标题"},
                    "abstract": {"type": "string", "description": "论文摘要"},
                    "contributions": {"type": "string", "description": "核心贡献（可选）"},
                },
                "required": ["title", "abstract"],
            },
        },
    },
    {
        "_meta": {"label": "保存论文卡片", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "save_paper_card",
            "description": "将论文卡片 Markdown 保存到 knowledge/。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "文件名，如 '2025-paper.md'"},
                    "content": {"type": "string", "description": "卡片 Markdown 内容"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "_meta": {"label": "扫描论文", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "scan_papers",
            "description": (
                "找最新论文的首选：走 arxiv 官方 API 按提交日期倒序检索，直接命中 arxiv 最新"
                "（找论文别用 search_web——网页搜索有索引滞后且偏旧）。"
                "输入JSON数组[[领域,搜索词],...]，过滤2025/2026、去重、缓存。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {"type": "string", "description": "JSON: [[\"领域\",\"搜索词\"],...]"},
                },
                "required": ["queries"],
            },
        },
    },
    {
        "_meta": {"label": "批量读论文", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "read_papers",
            "description": "批量读取arxiv标题+摘要。输入JSON数组[\"id1\",...]，最多30篇。返回结构化摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ids": {"type": "string", "description": "JSON: [\"2601.01528\",...]"},
                },
                "required": ["ids"],
            },
        },
    },
    {
        "_meta": {"label": "论文评分表", "plan_blocked": True},
        "type": "function",
        "function": {
            "name": "paper_grid",
            "description": "生成领域论文评分表Markdown。输入领域名+read_papers返回的JSON。写入knowledge/。",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "领域名"},
                    "papers_json": {"type": "string", "description": "read_papers 返回的 JSON"},
                },
                "required": ["area", "papers_json"],
            },
        },
    },
]

# ═══════════════════════════════════════════════════════════
# 内部函数
# ═══════════════════════════════════════════════════════════


def _arxiv_year(aid: str) -> str | None:
    """Arxiv id 形如 YYMM.NNNNN，前两位是年份。返回 '2025'/'2026'，否则 None。

    注意：不能用 startswith('250')——那只匹配月份 01-09，会漏掉 10/11/12 月
    （2510/2511/2512 这类 id），年份判定必须只看前两位。
    """
    if aid.startswith("25"):
        return "2025"
    if aid.startswith("26"):
        return "2026"
    return None


def _iter_query_pairs(query_list: list) -> list[tuple]:
    """从 [[领域, 搜索词], ...] 安全取出 (领域, 搜索词)，跳过结构不符的条目。

    LLM 可能给出畸形条目（元素数不对/不是列表），直接解包会抛 ValueError
    令整个 scan 崩溃；这里逐条校验、跳过坏条目，best-effort 处理其余。
    """
    pairs: list[tuple] = []
    for entry in query_list:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            pairs.append((entry[0], entry[1]))
    return pairs


def _parse_arxiv_feed(xml: str) -> list[tuple[str, str]]:
    """从 arxiv Atom feed 解析 [(arxiv_id, 提交日期), ...]，按 feed 内顺序。

    只取 <entry> 内的 id，避免误抓 feed 顶层的 self-link id。无 <published>
    时日期留空。结果顺序即 API 的排序（调用方用 submittedDate desc → 最新在前）。
    """
    out: list[tuple[str, str]] = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL):
        id_m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", entry)
        if not id_m:
            continue
        pub_m = re.search(r"<published>(\d{4}-\d{2}-\d{2})", entry)
        out.append((id_m.group(1), pub_m.group(1) if pub_m else ""))
    return out


def _arxiv_api_search(query: str, max_results: int = 20) -> list[tuple[str, str]]:
    """Arxiv 官方 API 按提交日期倒序检索，返回 [(id, 日期), ...]，最新在前。

    比 Tavily 网页搜索可靠地多：直接命中 arxiv、按日期倒序拿最新、无网页索引
    滞后、无页面引用噪声（网页搜索会把正文里引用的老论文 id 也抓出来）。
    网络失败返回空列表，由调用方容错。
    """
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    })
    req = urllib.request.Request(
        f"{_ARXIV_API}?{params}", headers={"User-Agent": "ResearchBot/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    return _parse_arxiv_feed(xml)


def _load_known_ids() -> set[str]:
    known: set[str] = set()
    kb_dir = _KNOWLEDGE_AD2026 / "topics"
    if kb_dir.is_dir():
        for md in kb_dir.glob("*.md"):
            known.update(re.findall(r'(\d{4}\.\d{4,5})', md.read_text(encoding="utf-8")))
    return known


def _read_arxiv_abstract(aid: str) -> dict:
    url = f"https://arxiv.org/abs/{aid}"
    req = urllib.request.Request(url, headers={"User-Agent": "ResearchBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {"id": aid, "title": "FETCH_ERR", "abstract": str(exc), "venue": "?"}

    title_m = re.search(r'Title:(.*?)(?:\n)', text)
    abstract_m = re.search(r'Abstract:(.*?)(?:Subjects|Comments|Cite|Submission)', text, re.DOTALL)
    comments_m = re.search(r'Comments:(.*?)(?:\n)', text)

    title = title_m.group(1).strip() if title_m else "?"
    abstract = abstract_m.group(1).strip() if abstract_m else ""
    venue = "arxiv"
    if comments_m:
        for conf in ["CVPR", "ICCV", "NeurIPS", "ICLR", "ICRA", "CoRL", "ECCV", "AAAI", "RSS"]:
            if conf in comments_m.group(1):
                yr = re.search(r'(\d{4})', comments_m.group(1))
                venue = f"{conf} {yr.group(1)}" if yr else conf
                break

    return {"id": aid, "title": title[:200], "abstract": abstract[:500], "venue": venue}

# ═══════════════════════════════════════════════════════════
# 新工具
# ═══════════════════════════════════════════════════════════

def scan_papers(queries: str) -> str:
    try:
        query_list: list = json.loads(queries)
    except json.JSONDecodeError as exc:
        return f"queries 解析失败: {exc}"

    known = _load_known_ids()
    found: dict[str, str] = {}

    # 用 arxiv 官方 API（按提交日期倒序）发现最新论文，而非 Tavily 网页搜索——
    # 网页搜索按相关度排序、有索引滞后、且会抓到正文引用的老论文 id。
    for area, raw_query in _iter_query_pairs(query_list):
        for aid, _pub in _arxiv_api_search(raw_query):
            if _arxiv_year(aid) and aid not in found:
                found[aid] = area
        time.sleep(3.0)  # arxiv API 礼貌限速（官方建议 ≥3s/次）

    new_papers = {k: v for k, v in found.items() if k not in known}
    overlap = {k: v for k, v in found.items() if k in known}
    y25 = sum(1 for k in new_papers if _arxiv_year(k) == "2025")
    y26 = sum(1 for k in new_papers if _arxiv_year(k) == "2026")

    lines = [
        f"搜索 {len(query_list)} 领域 → {len(found)} 篇",
        f"已知: {len(overlap)} | 新增: {len(new_papers)} (2025:{y25}, 2026:{y26})",
        f"总: {len(known) + len(new_papers)}",
    ]

    by_area = defaultdict(list)
    for aid, area_label in sorted(new_papers.items()):
        by_area[area_label].append(aid)

    for area_label, pid_list in sorted(by_area.items()):
        lines.append(f"\n## {area_label} ({len(pid_list)})")
        for pid in pid_list:
            lines.append(f"  [{(_arxiv_year(pid) or '????')[2:]}] {pid}")

    cache_path = _PROJECT_ROOT / ".scan_cache.json"
    cache_path.write_text(json.dumps(new_papers, ensure_ascii=False, indent=2), encoding="utf-8")
    lines.append("\n💾 已缓存 .scan_cache.json")

    return "\n".join(lines)


def read_papers(ids: str) -> str:
    try:
        id_list: list = json.loads(ids)
    except json.JSONDecodeError:
        cache_path = _PROJECT_ROOT / ".scan_cache.json"
        if cache_path.exists():
            id_list = list(json.loads(cache_path.read_text(encoding="utf-8")).keys())[:30]
        else:
            return "ids 解析失败且无缓存"

    id_list = id_list[:30]
    results = {}
    for _i, aid in enumerate(id_list):
        results[aid] = _read_arxiv_abstract(aid)
        time.sleep(0.8)

    output = json.dumps(results, ensure_ascii=False, indent=2)
    cache_path = _PROJECT_ROOT / ".read_cache.json"
    cache_path.write_text(output, encoding="utf-8")

    lines = [f"读取 {len(results)} 篇:"]
    for aid, info in results.items():
        lines.append(f"[{aid}] {info['title'][:100]} [{info['venue']}]")

    return "\n".join(lines) + "\n\n💾 已缓存 .read_cache.json"


def paper_grid(area: str, papers_json: str) -> str:
    try:
        papers: dict = json.loads(papers_json)
    except json.JSONDecodeError:
        cache_path = _PROJECT_ROOT / ".read_cache.json"
        if cache_path.exists():
            papers = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            return "papers_json 解析失败且无缓存"

    lines = [
        f"# {area} — 论文评分表",
        f"\n> {len(papers)} 篇 | R=关联 N=新颖 F=可行 I=影响\n",
        "| # | arxiv | 标题 | 会议 | R | N | F | I | 总 |",
        "|:--:|------|------|:---:|:--:|:--:|:--:|:--:|:--:|",
    ]

    for j, (aid, info) in enumerate(papers.items(), 1):
        title = info.get("title", "?")[:80]
        venue = info.get("venue", "arxiv")
        lines.append(
            f"| {j} | [{aid}](https://arxiv.org/abs/{aid}) "
            f"| {title} | {venue} | ? | ? | ? | ? | ? |"
        )

    result = "\n".join(lines)
    slug = area.replace("/", "-").replace(" ", "-").lower()
    path = _KNOWLEDGE_AD2026 / "topics" / f"grid-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result, encoding="utf-8")

    return result + f"\n\n💾 已保存 {path}"


# ═══════════════════════════════════════════════════════════
# 旧工具
# ═══════════════════════════════════════════════════════════

def analyze_paper(
    title: str, abstract: str, contributions: str = "",
    authors: str = "", conference: str = "", year: str = "",
) -> str:
    try:
        from paper_analyzer import classify_methodology, generate_card, match_gaps
    except ImportError as exc:
        return f"无法导入 paper_analyzer: {exc}"
    methodology = classify_methodology(title, abstract, contributions)
    gaps = match_gaps(title, abstract, contributions)
    parts: list[str] = []
    p = methodology["primary"]
    parts.append(f"## 方法论: {p['label']}（{p['confidence']:.0%}）")
    parts.append(f"- {p['description']} | 创新: {p['innovation_level']} | 风险: {p['risk']}")
    if "secondary" in methodology:
        s = methodology["secondary"]
        parts.append(f"- 次级: {s['label']}（{s['confidence']:.0%}）")
    parts.append("\n## Gap 匹配")
    if gaps:
        for g in gaps:
            parts.append(f"- {g['gap']} [{g['status']}]: {g['impact']}（{g['hit_count']} kw）")
    else:
        parts.append("⚠️ 未命中已知 Gap")
    parts.append("\n## 论文卡片模板\n")
    parts.append(generate_card(title, authors, conference, year, abstract, contributions, methodology, gaps))
    return "\n".join(parts)


def cross_validate(title: str, abstract: str, contributions: str = "") -> str:
    try:
        from cross_validator import generate_report
    except ImportError as exc:
        return f"无法导入 cross_validator: {exc}"
    try:
        report = generate_report(title, abstract, contributions)
    except Exception as exc:
        return f"交叉验证异常: {exc}"
    if isinstance(report, dict) and "error" in report:
        return f"交叉验证失败: {report['error']}"
    return str(report)


def save_paper_card(filename: str, content: str) -> str:
    filepath = _KNOWLEDGE_PAPERS / filename
    try:
        filepath = filepath.resolve()
    except (OSError, RuntimeError):
        return f"文件名无效: {filename}"
    if not str(filepath).startswith(str(_KNOWLEDGE_PAPERS.resolve())):
        return f"拒绝写入: {filename}"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    try:
        filepath.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"保存失败: {exc}"
    return f"✅ 已保存 knowledge/.../papers/{filename} ({len(content)} 字符)"


# ── 调度 ──────────────────────────────────────────────────

def execute(name: str, args: dict) -> str | None:
    if name == "analyze_paper":
        return analyze_paper(
            title=args["title"], abstract=args["abstract"],
            contributions=args.get("contributions", ""),
            authors=args.get("authors", ""),
            conference=args.get("conference", ""),
            year=args.get("year", ""),
        )
    if name == "cross_validate":
        return cross_validate(
            title=args["title"], abstract=args["abstract"],
            contributions=args.get("contributions", ""),
        )
    if name == "save_paper_card":
        return save_paper_card(filename=args["filename"], content=args["content"])
    if name == "scan_papers":
        return scan_papers(queries=args["queries"])
    if name == "read_papers":
        return read_papers(ids=args["ids"])
    if name == "paper_grid":
        return paper_grid(area=args["area"], papers_json=args["papers_json"])
    return None
