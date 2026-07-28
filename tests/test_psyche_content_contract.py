"""Psyche v1.2 的内容预算、分层与组合契约。"""

from __future__ import annotations

from src.cache_context import CacheContext
from src.psyche_bridge import inject_psyche, loaded_psyches_in_log
from src.psyche_catalog import load_catalog, resolve_load_order
from src.skill_catalog import load_skill_catalog


def test_every_core_and_dependency_stack_stay_within_context_budget():
    catalog = load_catalog()
    for spec in catalog.values():
        core = spec.core_path.read_text(encoding="utf-8")
        assert len(core) <= 3800, f"{spec.id} core 膨胀到 {len(core)} 字符"
        stack = resolve_load_order(spec.id, catalog)
        stack_chars = sum(len(item.core_path.read_text(encoding="utf-8")) for item in stack)
        assert stack_chars <= 10_000, f"{spec.id} 依赖栈膨胀到 {stack_chars} 字符"


def test_every_manifest_declares_real_judgment_delta_and_exit_checks():
    for spec in load_catalog().values():
        assert spec.judgment_delta, f"{spec.id} 未声明 judgment_delta"
        assert spec.exit_checks, f"{spec.id} 未声明 exit_checks"


def test_every_skill_has_exactly_one_owner_psyche():
    psyches = load_catalog()
    for skill_name in load_skill_catalog():
        owners = [spec.id for spec in psyches.values() if skill_name in spec.skills]
        assert len(owners) == 1, f"{skill_name} owners={owners}"


def test_distilled_cores_do_not_reabsorb_workflows_or_volatile_routes():
    catalog = load_catalog()
    walkthrough = catalog["paper-walkthrough"].core_path.read_text(encoding="utf-8")
    building = catalog["psyche-building"].core_path.read_text(encoding="utf-8")
    driving = catalog["autonomous-driving"].core_path.read_text(encoding="utf-8")

    assert "progress.md" not in walkthrough
    assert "tools/download_paper.py" not in walkthrough
    assert "write_file" not in building
    assert "典型耗时" not in building
    assert "GraphWorld" not in driving
    assert "Latent-WAM" not in driving


def test_taxonomy_and_composition_contracts_are_explicit():
    catalog = load_catalog()
    assert catalog["communication"].requires == ("general",)
    assert catalog["aesthetic-design"].requires == ("general",)
    assert set(catalog["tui-aesthetics"].requires) == {
        "software-development", "aesthetic-design",
    }
    assert catalog["paper-walkthrough"].conflicts == ("paper-co-read",)


def test_conflicting_modes_fail_without_partial_activation():
    ctx = CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])
    assert inject_psyche(
        ctx, "paper-co-read", source="agent", reason="需要对等推演",
    ).startswith("✅")
    before = list(ctx.log)

    result = inject_psyche(
        ctx, "paper-walkthrough", source="agent", reason="需要渐进带读",
    )

    assert "冲突" in result
    assert ctx.log == before
    active = {meta.get("id") or meta.get("name") for meta in loaded_psyches_in_log(ctx)}
    assert "paper-walkthrough" not in active
