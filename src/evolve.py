"""进化档案 — 记录每次自修改尝试，支持查询和学习。

每条记录以 JSONL 格式存储到 ~/.ctgents/evolution/evolution.jsonl。
支持：写入记录、关键词搜索、TF-IDF 相似度搜索、教训提取、统计汇总。
"""

import contextlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from math import log
from pathlib import Path

EVOLVE_DIR = Path.home() / ".ctgents" / "evolution"
EVOLVE_LOG = EVOLVE_DIR / "evolution.jsonl"
MAX_RECORDS = 1000


@dataclass
class EvolutionRecord:
    """一次自进化尝试的完整记录。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    goal: str = ""
    research_sources: list[dict] = field(default_factory=list)
    candidate_approaches: list[dict] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""
    pre_commit_result: str | None = None
    sandbox_test_result: str | None = None
    post_test_result: str | None = None
    outcome: str = "unknown"  # merged | reverted | partial
    git_commit_before: str = ""
    git_commit_after: str | None = None
    lessons_learned: str = ""
    tags: list[str] = field(default_factory=list)
    coverage_impact: dict = field(default_factory=dict)
    duration_total_ms: float = 0.0


# ── 写入 ──

def _ensure_dir() -> None:
    EVOLVE_DIR.mkdir(parents=True, exist_ok=True)


def record_attempt(record: EvolutionRecord) -> str:
    """写入一条进化记录。返回记录 ID。"""
    _ensure_dir()
    d = asdict(record)
    try:
        with open(EVOLVE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    except OSError:
        pass
    _trim_if_needed()
    return record.id


# ── 查询 ──

def _read_all() -> list[dict]:
    """全量读取（仅在需要全量统计时使用）。"""
    if not EVOLVE_LOG.exists():
        return []
    records = []
    try:
        with open(EVOLVE_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        records.append(json.loads(line))
    except OSError:
        pass
    return records


def _read_tail(limit: int) -> list[dict]:
    """尾部读取 N 条（O(1) 内存）。"""
    if not EVOLVE_LOG.exists():
        return []
    try:
        with open(EVOLVE_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return []
            chunk = min(size, max(8192, limit * 512))
            f.seek(-chunk, os.SEEK_END)
            raw = f.read().decode("utf-8", errors="replace")
            if raw and raw[0] != "{":
                nl = raw.find("\n")
                if nl != -1:
                    raw = raw[nl + 1:]
            lines = raw.strip().splitlines()
    except OSError:
        return []

    records = []
    for line in lines[-limit:]:
        if line:
            with contextlib.suppress(json.JSONDecodeError):
                records.append(json.loads(line))
    return records


def query(goal_keywords: list[str] | None = None,
          outcome: str | None = None,
          tags: list[str] | None = None,
          limit: int = 20) -> list[dict]:
    """按条件搜索进化记录。尾部读取，匹配后返回最多 limit 条。"""
    records = _read_tail(max(100, limit * 3))
    result = []

    for r in reversed(records):
        if len(result) >= limit:
            break
        # 按 outcome 筛选
        if outcome and r.get("outcome") != outcome:
            continue
        # 按 tag 筛选
        if tags:
            rec_tags = set(r.get("tags", []))
            if not rec_tags.intersection(tags):
                continue
        # 按关键词筛选 goal
        if goal_keywords:
            goal_lower = r.get("goal", "").lower()
            if not any(kw.lower() in goal_lower for kw in goal_keywords):
                continue
        result.append(r)

    return result


# ── 相似度搜索 ──

def _tokenize(text: str) -> list[str]:
    """分词：英文按空格+标点切分，中文用字符二元组（bigram）。

    中文无空格分隔，字符二元组是最简有效的跨语言检索方式。
    """
    # 分离中文字符和非中文字符
    cjk = re.findall(r"[一-鿿㐀-䶿]+", text.lower())
    non_cjk = re.sub(r"[一-鿿㐀-䶿]+", " ", text.lower())
    non_cjk = re.sub(r"[^\w\s]", " ", non_cjk)

    tokens: list[str] = []
    # 英文 token
    for t in non_cjk.split():
        if len(t) >= 2:
            tokens.append(t)
    # 中文字符二元组
    for segment in cjk:
        for i in range(len(segment) - 1):
            tokens.append(segment[i:i + 2])
        # 单字也保留
        if len(segment) == 1:
            tokens.append(segment)

    return tokens


def _tfidf_search(query_text: str, documents: list[dict], field: str = "goal",
                  top_n: int = 5) -> list[dict]:
    """TF-IDF 相似度搜索。"""
    if not documents:
        return []

    # 构建词表
    query_tokens = _tokenize(query_text)
    doc_tokens_list = [_tokenize(d.get(field, "")) for d in documents]
    all_tokens = set(query_tokens)
    for dt in doc_tokens_list:
        all_tokens.update(dt)

    # IDF
    doc_count = len(documents)
    idf: dict[str, float] = {}
    for token in all_tokens:
        df = sum(1 for dt in doc_tokens_list if token in dt)
        idf[token] = log((doc_count + 1) / (df + 1)) + 1.0

    # 查询 TF
    query_tf = Counter(query_tokens)

    # 计算每个文档的余弦相似度
    scores: list[tuple[int, float]] = []
    query_norm = sum((query_tf[t] * idf.get(t, 0)) ** 2 for t in query_tokens) ** 0.5
    if query_norm == 0:
        return []

    for idx, doc_tokens in enumerate(doc_tokens_list):
        doc_tf = Counter(doc_tokens)
        dot = sum(query_tf.get(t, 0) * idf.get(t, 0) * doc_tf.get(t, 0) * idf.get(t, 0)
                  for t in set(query_tokens) & set(doc_tokens))
        doc_norm = sum((doc_tf[t] * idf.get(t, 0)) ** 2 for t in doc_tokens) ** 0.5
        if doc_norm > 0 and query_norm > 0:
            scores.append((idx, dot / (query_norm * doc_norm)))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [documents[i] for i, _s in scores[:top_n] if _s > 0]


def find_similar(goal: str, top_n: int = 5) -> list[dict]:
    """查找与目标最相似的历史进化记录。"""
    documents = _read_tail(200)
    if not documents:
        return []
    return _tfidf_search(goal, documents, field="goal", top_n=top_n)


# ── 统计 ──

def get_stats() -> dict:
    """进化档案汇总统计。"""
    records = _read_all()
    if not records:
        return {"total_attempts": 0, "message": "暂无进化记录"}

    total = len(records)
    merged = sum(1 for r in records if r.get("outcome") == "merged")
    reverted = sum(1 for r in records if r.get("outcome") == "reverted")
    partial = sum(1 for r in records if r.get("outcome") == "partial")

    # 按 tag 统计
    tag_counter: Counter = Counter()
    for r in records:
        for t in r.get("tags", []):
            tag_counter[t] += 1

    # 平均耗时
    durations = [r.get("duration_total_ms", 0) for r in records
                 if r.get("duration_total_ms", 0) > 0]
    avg_duration = sum(durations) / len(durations) if durations else 0

    return {
        "total_attempts": total,
        "merged": merged,
        "reverted": reverted,
        "partial": partial,
        "success_rate": round(merged / total * 100, 1) if total else 0,
        "top_tags": tag_counter.most_common(10),
        "avg_duration_ms": round(avg_duration, 1),
        "last_attempt": records[-1].get("timestamp", "") if records else "",
    }


def get_last_n(n: int = 10) -> list[dict]:
    """返回最近 N 条进化记录（简要版），最新在前。"""
    records = _read_tail(n)
    records.reverse()  # 最新在前
    return [{
        "id": r.get("id", ""),
        "timestamp": r.get("timestamp", ""),
        "goal": r.get("goal", "")[:100],
        "outcome": r.get("outcome", ""),
        "files_changed": r.get("files_changed", []),
        "lessons_learned": r.get("lessons_learned", "")[:200],
    } for r in records]


# ── 维护 ──

def _trim_if_needed():
    """记录过多时截断。"""
    try:
        if not EVOLVE_LOG.exists():
            return
        size = EVOLVE_LOG.stat().st_size
        if size < 500_000:
            return
        with open(EVOLVE_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_RECORDS:
            with open(EVOLVE_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_RECORDS // 2:])
    except OSError:
        pass
