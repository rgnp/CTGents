"""Psyche manifest Catalog、依赖 DAG 与能力目录。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.psyche_catalog import (
    PsycheCatalogError,
    catalog_text,
    load_catalog,
    resolve_load_order,
)


def test_real_catalog_discovers_all_psyches_and_deep_children():
    catalog = load_catalog()
    assert len(catalog) >= 18
    assert "casual-chat" in catalog
    assert "learning-method" in catalog


def test_real_catalog_resolves_explicit_dependency_chains():
    catalog = load_catalog()
    assert [spec.id for spec in resolve_load_order("paper-deep-read", catalog)] == [
        "general", "research", "paper-deep-read",
    ]
    assert [spec.id for spec in resolve_load_order("learning-method", catalog)] == [
        "general", "software-development", "psyche-building", "learning-method",
    ]


def _write_psyche(root: Path, psyche_id: str, *, requires: list[str] | None = None,
                   skills: list[str] | None = None,
                   conflicts: list[str] | None = None) -> None:
    directory = root / psyche_id
    directory.mkdir(parents=True)
    (directory / "core.md").write_text(f"# {psyche_id}\n", encoding="utf-8")
    deps = "[" + ", ".join(requires or []) + "]"
    skill_list = "[" + ", ".join(skills or []) + "]"
    conflict_list = "[" + ", ".join(conflicts or []) + "]"
    (directory / "manifest.yaml").write_text(
        "\n".join([
            f"id: {psyche_id}",
            'version: "1.0"',
            "kind: domain",
            "core: core.md",
            f"requires: {deps}",
            "scope_default: task",
            f"summary: {psyche_id}",
            "judgment_delta: []",
            f"skills: {skill_list}",
            f"conflicts: {conflict_list}",
            "exit_checks: []",
        ]) + "\n",
        encoding="utf-8",
    )


def test_catalog_rejects_dependency_cycle(tmp_path):
    root = tmp_path / "psyche"
    _write_psyche(root, "a", requires=["b"])
    _write_psyche(root, "b", requires=["a"])

    with pytest.raises(PsycheCatalogError, match="依赖成环"):
        load_catalog(root, tmp_path / "skills")


def test_catalog_rejects_missing_skill(tmp_path):
    root = tmp_path / "psyche"
    _write_psyche(root, "a", skills=["ghost-skill"])

    with pytest.raises(PsycheCatalogError, match="Skill 不存在"):
        load_catalog(root, tmp_path / "skills")


def test_resolver_skips_already_active_dependencies():
    catalog = load_catalog()
    order = resolve_load_order("learning-method", catalog, {"general", "software-development"})
    assert [spec.id for spec in order] == ["psyche-building", "learning-method"]


def test_catalog_rejects_asymmetric_conflict(tmp_path):
    root = tmp_path / "psyche"
    _write_psyche(root, "a", conflicts=["b"])
    _write_psyche(root, "b")

    with pytest.raises(PsycheCatalogError, match="必须双向声明"):
        load_catalog(root, tmp_path / "skills")


def test_resolver_rejects_conflict_with_active_psyche(tmp_path):
    root = tmp_path / "psyche"
    _write_psyche(root, "a", conflicts=["b"])
    _write_psyche(root, "b", conflicts=["a"])
    catalog = load_catalog(root, tmp_path / "skills")

    with pytest.raises(PsycheCatalogError, match="冲突"):
        resolve_load_order("a", catalog, {"b"})


def test_catalog_text_exposes_judgment_delta_without_loading():
    text = catalog_text(load_catalog(), "warrant")
    assert "paper-deep-read" in text
    assert "自动加载" in text
