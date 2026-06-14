"""每轮对话前 embedding 语义检索记忆（替换旧 token 匹配）。

旧 _prerecall_memory 用 Jaccard/token-overlap 做匹配——无法处理同义词、
跨语言、语义相关但不共享词汇的记忆。改为 sentence-transformers embedding，
cosine 相似度排序。

模型：all-MiniLM-L6-v2（384d，80MB，加载 <2s，中英双语可用）。
首次加载下载模型，之后重启秒开。embedding 缓存到 memory/.embeddings/，
改文件后增量更新。

用法：process_turn 内 inject_prerecall 自动调用，外部不可见。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

# ── 模型懒加载（模块级单例，首次调用时下载模型，之后缓存 ──
_EMBEDDING_MODEL: object | None = None
_EMBEDDING_MODEL_NAME = os.getenv("CTG_MEMORY_EMBEDDING_MODEL", "all-MiniLM-L6-v2")


def _get_model():
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _EMBEDDING_MODEL = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _EMBEDDING_MODEL


# ── embedding 缓存：memory 文件 → (mtime, embedding) ──
_EMBEDDING_CACHE: dict[str, tuple[float, np.ndarray]] = {}
_EMBEDDING_CACHE_LOADED = False


def _cache_dir() -> Path:
    from .config import MEMORY_DIR
    d = Path(MEMORY_DIR) / ".embeddings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path() -> Path:
    return _cache_dir() / "cache.json"


def _load_embedding_cache() -> None:
    """从磁盘加载 embedding 缓存。"""
    global _EMBEDDING_CACHE, _EMBEDDING_CACHE_LOADED
    _EMBEDDING_CACHE_LOADED = True
    cp = _cache_path()
    if not cp.exists():
        return
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        for key, (mtime, emb_list) in data.items():
            _EMBEDDING_CACHE[key] = (mtime, np.array(emb_list, dtype=np.float32))
    except Exception:
        _EMBEDDING_CACHE = {}


def _save_embedding_cache() -> None:
    """写入 embedding 缓存到磁盘。"""
    data: dict[str, tuple[float, list[float]]] = {}
    for key, (mtime, emb) in _EMBEDDING_CACHE.items():
        data[key] = (mtime, emb.tolist())
    _cache_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _ensure_cache_loaded() -> None:
    if not _EMBEDDING_CACHE_LOADED:
        _load_embedding_cache()


def _prerecall_memory_embedding(user_input: str) -> str | None:
    """用 embedding 语义检索记忆，返回注入文本或 None。"""
    from .config import MEMORY_DIR
    from .tools.memory import _SNIPPET_CHARS, _split_frontmatter

    q_lower = user_input.strip()
    if len(q_lower) < 3:
        return None

    _ensure_cache_loaded()
    mem_dir = Path(MEMORY_DIR)

    # ── 收集需要 (重)编码的文件 ──
    to_encode: list[str] = []  # 文件路径
    scored: list[tuple[float, str, str, str, str]] = []  # (sim, updated, name, type, snippet)
    cached_results: list[tuple[float, str, str, str, str]] = []

    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            full = f.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, body = _split_frontmatter(full)
        if meta.get("severity"):
            continue

        fpath = str(f)
        current_mtime = f.stat().st_mtime

        # 缓存命中 → 暂存，等 query embedding 算完再批量算 cosine
        if fpath in _EMBEDDING_CACHE:
            cached_mtime, cached_emb = _EMBEDDING_CACHE[fpath]
            if abs(cached_mtime - current_mtime) < 0.01:
                snippet = body[:_SNIPPET_CHARS].replace("\n", " ")
                cached_results.append((
                    cached_emb, meta.get("updated", ""), f.stem,
                    meta.get("type", "") or "", snippet,
                ))
                continue

        # 缓存未命中或已过期 → 需要重新编码
        to_encode.append(fpath)

    # ── 编码 query + 未缓存文件 ──
    model = _get_model()
    query_emb = model.encode([user_input], normalize_embeddings=True)[0]

    # 批量编码未缓存文件（用 name + body 前 500 字）
    if to_encode:
        texts_to_encode: list[str] = []
        file_info: list[tuple[str, float, str, str, str]] = []
        for fpath in to_encode:
            try:
                f = Path(fpath)
                full = f.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _split_frontmatter(full)
            if meta.get("severity"):
                continue
            # 编码用：name + description + body 前 500 字
            text = f"{meta.get('name', f.stem)}: {meta.get('description', '')} {body[:500]}"
            texts_to_encode.append(text)
            snippet = body[:_SNIPPET_CHARS].replace("\n", " ")
            file_info.append((
                fpath, f.stat().st_mtime,
                meta.get("updated", ""), f.stem,
                meta.get("type", "") or "", snippet,
            ))

        if texts_to_encode:
            new_embs = model.encode(texts_to_encode, normalize_embeddings=True)
            for i, emb in enumerate(new_embs):
                fpath, mtime, updated, stem, mtype, snippet = file_info[i]
                _EMBEDDING_CACHE[fpath] = (mtime, emb)
                sim = float(np.dot(query_emb, emb))
                scored.append((sim, updated, stem, mtype, snippet))

    # ── 对缓存命中的文件算 cosine ──
    for emb, updated, stem, mtype, snippet in cached_results:
        sim = float(np.dot(query_emb, emb))
        scored.append((sim, updated, stem, mtype, snippet))

    # ── 持久化缓存（有新编码时）──
    if to_encode:
        _save_embedding_cache()

    if not scored:
        return None

    # ── 按相似度排序，取 top-3 ──
    scored.sort(key=lambda r: r[0], reverse=True)
    top = [(s, u, n, t, sn) for s, u, n, t, sn in scored if s > 0.2][:3]
    if not top:
        return None

    lines = [(
        "[自动记忆检索] embedding 语义匹配（非主动 recall；"
        "如有用请用 recall 深读）："
    )]
    for sim, _u, name, mtype, snippet in top:
        tag = f" [{mtype}]" if mtype else ""
        lines.append(f"  📌 {name}{tag} (sim={sim:.2f}): {snippet[:200]}")
    return "\n".join(lines)


_AUTO_RECALL_CACHE: dict[str, str | None] = {}
_AUTO_RECALL_CACHE_MAX = 32


def prerecall_for_turn(user_input: str) -> str | None:
    """对 user_input 做 embedding 语义检索（带缓存，同输入不重复搜）。"""
    key = user_input[:200]
    if key in _AUTO_RECALL_CACHE:
        return _AUTO_RECALL_CACHE[key]
    result = _prerecall_memory_embedding(user_input)
    _AUTO_RECALL_CACHE[key] = result
    if len(_AUTO_RECALL_CACHE) > _AUTO_RECALL_CACHE_MAX:
        _AUTO_RECALL_CACHE.pop(next(iter(_AUTO_RECALL_CACHE)))
    return result


def inject_prerecall(ctx, user_input: str) -> None:
    """在 ctx.log 尾部注入自动记忆检索结果（strip-then-append，缓存安全）。"""
    ctx.log[:] = [m for m in ctx.log if not m.get("_auto_recall")]
    result = prerecall_for_turn(user_input)
    if result:
        ctx.log.append({
            "role": "system", "content": result,
            "_volatile": True, "_auto_recall": True,
        })
