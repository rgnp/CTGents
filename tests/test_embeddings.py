"""embeddings.py 单测：懒加载降级路径 + 落盘/读取往返 + 维度校验。

真实 sentence-transformers 模型不参与测试（下载/CPU 推理慢，CI 也不一定有网）——
全部用可控的伪造 encoder 替身，只测本模块自己的落盘/降级/维度校验逻辑，
不测第三方库本身的编码质量。
"""
from __future__ import annotations

import builtins

import numpy as np
import pytest

from src.tools import embeddings


class _FakeModel:
    """确定性伪 encoder：按文本关键词映射到固定二维向量，模拟真实语义相似度排序。"""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        vecs = [([1.0, 0.0] if "world model" in t or "占据栅格" in t else [0.0, 1.0]) for t in texts]
        return np.array(vecs, dtype=np.float32)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """每个测试独立的单例状态，防止一个测试注入的假模型泄漏到下一个。"""
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setattr(embeddings, "_model_load_failed", False)


def test_available_false_when_sentence_transformers_missing(monkeypatch):
    """真实环境没装 sentence-transformers 时，available()/encode() 必须静默降级而不是抛异常。"""
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    assert embeddings.available() is False
    assert embeddings.encode(["x"]) is None


def test_disabled_via_param(monkeypatch):
    """CTG_RAG_EMBED_ENABLED=0 时整层直接短路，不触发任何导入。"""
    from dataclasses import replace

    from src.params import RAG

    monkeypatch.setattr(embeddings, "RAG", replace(RAG, embed_enabled=False))
    assert embeddings.available() is False


def test_encode_with_injected_model(monkeypatch):
    monkeypatch.setattr(embeddings, "_model", _FakeModel())
    vecs = embeddings.encode(["a world model paper", "occupancy prediction note"])
    assert vecs is not None
    assert vecs.shape == (2, 2)


def test_build_and_query_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_EMBED_DIR", tmp_path)
    monkeypatch.setattr(embeddings, "_model", _FakeModel())

    ok = embeddings.try_build_index("_test_idx", ["a world model paper", "occupancy prediction note"])
    assert ok is True
    assert (tmp_path / "_test_idx.embeddings.npy").exists()

    scores = embeddings.query_scores("_test_idx", "world model", expected_rows=2)
    assert scores is not None
    assert scores[0] > scores[1]  # 第一条文档语义匹配查询，分数理应更高


def test_query_scores_row_mismatch_returns_none(tmp_path, monkeypatch):
    """已存索引和当前 chunks 数对不上（知识库改过但索引没重建）→ 视为过期，交回调用方重建。"""
    monkeypatch.setattr(embeddings, "_EMBED_DIR", tmp_path)
    monkeypatch.setattr(embeddings, "_model", _FakeModel())
    embeddings.try_build_index("_test_idx2", ["only one doc"])
    assert embeddings.query_scores("_test_idx2", "query", expected_rows=5) is None


def test_query_scores_missing_index(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_EMBED_DIR", tmp_path)
    assert embeddings.query_scores("_never_built", "query", expected_rows=1) is None


def test_try_build_index_returns_false_when_model_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "_EMBED_DIR", tmp_path)
    monkeypatch.setattr(embeddings, "_model_load_failed", True)  # 模拟已知不可用
    assert embeddings.try_build_index("_test_idx3", ["some text"]) is False
    assert not (tmp_path / "_test_idx3.embeddings.npy").exists()
