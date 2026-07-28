"""Knowledge source lifecycle: edits/deletions invalidate indexes and audits stay read-only."""

from __future__ import annotations

import json

import pytest

from src import asset_usage
from src.tools import rag


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    root = tmp_path / "knowledge"
    root.mkdir()
    monkeypatch.setattr(rag, "_KNOWLEDGE_DIR", root)
    monkeypatch.setattr(asset_usage, "USAGE_FILE", tmp_path / "asset-usage.jsonl")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rag.embeddings, "_model", None)
    monkeypatch.setattr(rag.embeddings, "_model_load_failed", True)
    return root


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text * 4, encoding="utf-8")


def test_deleted_source_is_removed_from_lazy_index(knowledge):
    removed = knowledge / "topic" / "removed.md"
    kept = knowledge / "topic" / "kept.md"
    _write(removed, "obsolete_marker knowledge that should disappear. ")
    _write(kept, "durable_marker knowledge that should remain. ")
    assert "removed" in rag.query_research("obsolete_marker")

    removed.unlink()
    result = rag.query_research("obsolete_marker")

    assert "未找到" in result
    index = rag._load_doc_index("research")
    assert index is not None
    assert all("removed.md" not in chunk["source"] for chunk in index["chunks"])


def test_empty_source_clears_old_research_index(knowledge):
    source = knowledge / "only.md"
    _write(source, "temporary_marker research content. ")
    assert "only" in rag.query_research("temporary_marker")

    source.unlink()
    result = rag.query_research("temporary_marker")

    assert "知识库为空" in result
    assert rag._load_doc_index("research") is None


def test_missing_knowledge_directory_clears_old_index(knowledge):
    source = knowledge / "only.md"
    _write(source, "temporary_marker research content. ")
    rag.index_research_content()
    source.unlink()
    knowledge.rmdir()

    result = rag.query_research("temporary_marker")

    assert "知识库为空" in result
    assert rag._load_doc_index("research") is None


def test_retired_sources_are_excluded(knowledge):
    _write(knowledge / "_retired" / "old.md", "retired_marker must not be indexed. ")
    _write(knowledge / "active.md", "active_marker should be indexed. ")

    rag.index_research_content()
    index = rag._load_doc_index("research")

    assert index is not None
    sources = [chunk["source"] for chunk in index["chunks"]]
    assert all("_retired" not in source for source in sources)


def test_research_index_reports_source_files_not_chunks(knowledge):
    _write(knowledge / "long.md", "paragraph one with enough content. \n\n" * 20)
    result = rag.index_research_content()
    assert "1 篇知识库文档" in result


def test_knowledge_audit_reports_duplicates_short_and_broken_registry(knowledge):
    duplicate = "same exact document body with enough content for lifecycle audit. " * 3
    _write(knowledge / "a.md", duplicate)
    (knowledge / "b.md").write_text((knowledge / "a.md").read_text(encoding="utf-8"), encoding="utf-8")
    (knowledge / "short.md").write_text("tiny", encoding="utf-8")
    registry = knowledge / "_registry"
    registry.mkdir()
    (registry / "domain.json").write_text(
        json.dumps({"cards": {"x": {"path": "knowledge/missing.md"}}}),
        encoding="utf-8",
    )

    result = rag.knowledge_audit()

    assert "精确重复组: 1" in result
    assert "short.md" in result
    assert "knowledge/missing.md" in result


def test_knowledge_audit_execute_route(knowledge):
    assert "知识资产审计" in rag.execute("knowledge_audit", {})
