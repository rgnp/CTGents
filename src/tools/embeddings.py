"""本地 embedding 编码 + 余弦检索：给 rag.py 的研究文档索引提供语义召回。

纯本地、离线，不要 API key（sentence-transformers 首次调用会下载模型权重到本地缓存，
之后不再联网）。这一层只加分，不兜底——sentence-transformers 未安装、模型加载失败、
编码过程异常，一律静默降级返回 None，调用方（rag.py）保留 TF-IDF 作为可靠基线。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..params import RAG

if TYPE_CHECKING:
    import numpy as np

# 单一真相源 = params.RAG.index_dir（与 rag.py 的 RAG_INDEX_DIR 同源），cwd 相对、故意不用
# __file__ 锚定——必须和词面索引落在同一目录，否则词面/向量索引分裂到两个位置。
# 测试要用 monkeypatch.chdir 隔离，不隔离会写进真实项目目录（rag.py 测试注释里已踩过这个坑）。
_EMBED_DIR = Path(RAG.index_dir)

_model = None
_model_load_failed = False


def _get_model() -> Any | None:
    """懒加载 SentenceTransformer 单例。失败一次后不再重试（避免每次调用都触发慢导入）。"""
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if _model_load_failed or not RAG.embed_enabled:
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        _model_load_failed = True
        return None
    try:
        _model = SentenceTransformer(RAG.embed_model)
    except Exception:
        _model_load_failed = True
        return None
    return _model


def available() -> bool:
    """Embedding 层是否可用（模型已装且能加载）。供 rag_status 之类的诊断展示用。"""
    return _get_model() is not None


def encode(texts: list[str]) -> np.ndarray | None:
    """文本列表 → 归一化向量矩阵（行范数=1，点积即余弦相似度）。不可用时返回 None。"""
    model = _get_model()
    if model is None:
        return None
    truncated = [t[: RAG.embed_max_chars] for t in texts]
    try:
        import numpy as np

        vectors = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vectors, dtype=np.float32)
    except Exception:
        return None


def _index_path(index_name: str) -> Path:
    return _EMBED_DIR / f"{index_name}.embeddings.npy"


def try_build_index(index_name: str, texts: list[str]) -> bool:
    """编码一批文本并落盘到 `.rag-index/{index_name}.embeddings.npy`。

    任何一步失败都吞掉异常返回 False——这一层从不让 rag.py 的 TF-IDF 索引流程中断。
    """
    vectors = encode(texts)
    if vectors is None:
        return False
    try:
        import numpy as np

        _EMBED_DIR.mkdir(exist_ok=True)
        np.save(_index_path(index_name), vectors)
        return True
    except (OSError, ImportError):
        return False


def query_scores(index_name: str, query: str, expected_rows: int) -> np.ndarray | None:
    """Query 相对已存索引每个 chunk 的余弦相似度数组，行数对不上（过期/损坏）或不可用时返回 None。"""
    path = _index_path(index_name)
    if not path.exists():
        return None
    try:
        import numpy as np

        doc_vectors = np.load(path)
    except Exception:
        return None
    if doc_vectors.shape[0] != expected_rows:
        return None
    q_vec = encode([query])
    if q_vec is None:
        return None
    return doc_vectors @ q_vec[0]
