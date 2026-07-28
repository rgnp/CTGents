from __future__ import annotations

from src.cache_context import CacheContext
from src.psyche_bridge import inject_psyche, remove_psyche
from src.skill_bridge import activate_skill, loaded_skills
from src.skill_catalog import SkillCatalogError, load_skill_catalog, render_skill
from src.tools.control import execute


def _ctx() -> CacheContext:
    return CacheContext(prefix_msgs=[{"role": "system", "content": "sys"}])


def test_project_skill_catalog_and_defaults():
    catalog = load_skill_catalog()
    assert catalog.keys() >= {
        "build-psyche", "design-tests", "paper-deep-read",
        "paper-walkthrough", "review-interface",
    }

    content, axes = render_skill(catalog["paper-walkthrough"])

    assert axes == {"depth": "standard"}
    assert "本 Skill 不加载 Psyche" in content


def test_skill_axis_rejects_unknown_value():
    spec = load_skill_catalog()["paper-walkthrough"]
    try:
        render_skill(spec, {"depth": "impossible"})
    except SkillCatalogError as exc:
        assert "无效" in str(exc)
    else:
        raise AssertionError("invalid axis must fail")


def test_owner_psyche_must_be_active_and_failure_is_zero_write():
    ctx = _ctx()
    before = list(ctx.log)

    result = activate_skill(ctx, "paper-walkthrough", reason="开始带读")

    assert "owner 未激活" in result
    assert ctx.log == before


def test_active_owner_can_activate_owned_skill():
    ctx = _ctx()
    inject_psyche(ctx, "paper-walkthrough", source="agent", reason="需要渐进建立理解")

    result = activate_skill(
        ctx, "paper-walkthrough", axes={"depth": "close"}, reason="逐段带读",
    )

    assert result.startswith("✅")
    assert loaded_skills(ctx)[0]["owner_psyche"] == "paper-walkthrough"
    assert loaded_skills(ctx)[0]["axes"] == {"depth": "close"}
    assert ctx.log[-1]["_skill_event"]["type"] == "activate"


def test_psyche_stop_deactivates_owned_skill_first():
    ctx = _ctx()
    inject_psyche(ctx, "paper-walkthrough", source="agent", reason="需要渐进建立理解")
    activate_skill(ctx, "paper-walkthrough", reason="执行带读")

    result = remove_psyche(ctx, "paper-walkthrough")

    assert result.startswith("✅")
    assert loaded_skills(ctx) == []
    assert ctx.log[-2]["_skill_event"]["type"] == "deactivate"
    assert ctx.log[-1]["_psyche_event"]["type"] == "deactivate"


def test_activate_skill_control_request_requires_reason():
    assert "必须说明用途" in execute("activate_skill", {"name": "paper-walkthrough"})
    assert "请求激活" in execute(
        "activate_skill", {"name": "paper-walkthrough", "reason": "执行带读"},
    )


def test_new_workflow_skills_are_owned_and_activatable():
    cases = [
        ("psyche-building", "build-psyche", {"operation": "refine"}),
        ("aesthetic-design", "review-interface", {"medium": "tui", "goal": "diagnose"}),
        ("testing", "design-tests", {"operation": "review"}),
    ]
    for psyche, skill, axes in cases:
        ctx = _ctx()
        assert inject_psyche(ctx, psyche, source="agent", reason="需要专门判断").startswith("✅")
        assert activate_skill(ctx, skill, axes=axes, reason="执行对应流程").startswith("✅")
        assert loaded_skills(ctx)[0]["owner_psyche"] == psyche
