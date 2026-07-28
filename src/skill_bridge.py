"""Psyche 拥有的 Skill 激活与生命周期桥。"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from .psyche_catalog import PsycheCatalogError, load_catalog
from .skill_catalog import SkillCatalogError, load_skill_catalog, render_skill

if TYPE_CHECKING:
    from .cache_context import CacheContext


def loaded_skills(ctx: CacheContext) -> list[dict]:
    """从 append-only 事件归约当前 active Skills。"""
    active: dict[str, dict] = {}
    for message in ctx.log:
        event = message.get("_skill_event")
        if not event:
            continue
        name = event.get("name")
        if not name:
            continue
        if event.get("type") == "deactivate":
            active.pop(name, None)
        elif event.get("type") == "activate":
            active[name] = dict(event)
    return list(active.values())


def _owner_for(skill_name: str) -> str:
    psyche_catalog = load_catalog()
    owners = sorted(spec.id for spec in psyche_catalog.values() if skill_name in spec.skills)
    if not owners:
        raise SkillCatalogError(f"Skill {skill_name} 没有 owner Psyche")
    if len(owners) > 1:
        raise SkillCatalogError(f"Skill {skill_name} 有多个 owner Psyche: {', '.join(owners)}")
    return owners[0]


def activate_skill(
    ctx: CacheContext,
    name: str,
    *,
    axes: dict[str, str] | None = None,
    reason: str = "",
) -> str:
    """仅当 owner Psyche active 时，装配并 append Skill 指令。"""
    try:
        catalog = load_skill_catalog()
        spec = catalog[name]
        owner = _owner_for(name)
    except KeyError:
        return f"❌ 找不到 Skill '{name}'。可用: {', '.join(sorted(catalog)) or '（无）'}"
    except (SkillCatalogError, PsycheCatalogError) as exc:
        return f"❌ Skill Catalog 无效: {exc}"

    from .psyche_bridge import loaded_psyches_in_log

    active_psyches = {meta.get("id") or meta.get("name") for meta in loaded_psyches_in_log(ctx)}
    if owner not in active_psyches:
        return f"❌ Skill {name} 只能由 active Psyche {owner} 调用；当前 owner 未激活。"
    if any(event.get("name") == name for event in loaded_skills(ctx)):
        return f"⚠️ Skill {name} 已激活。"

    try:
        content, selected = render_skill(spec, axes)
    except (OSError, SkillCatalogError) as exc:
        return f"❌ Skill 激活失败（零写入）: {exc}"

    event = {
        "type": "activate",
        "name": name,
        "version": spec.version,
        "owner_psyche": owner,
        "axes": selected,
        "reason": reason,
        "activation_id": uuid.uuid4().hex[:12],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
    }
    ctx.log.append({
        "role": "system",
        "content": (
            f"【Skill activated: {name} v{spec.version} | owner={owner} | "
            f"axes={json.dumps(selected, ensure_ascii=False)}】\n\n{content}"
        ),
        "_skill_event": event,
    })
    return f"✅ Psyche {owner} 已激活 Skill {name}（axes={selected}）。"


def deactivate_skills_for_owner(ctx: CacheContext, owner: str) -> list[str]:
    """Psyche 停用前，先 append 停用其拥有的 active Skills。"""
    results = []
    for previous in reversed(loaded_skills(ctx)):
        if previous.get("owner_psyche") != owner:
            continue
        name = previous["name"]
        event = {
            "type": "deactivate",
            "name": name,
            "version": previous.get("version", "?"),
            "owner_psyche": owner,
            "activation_id": previous.get("activation_id", "?"),
        }
        ctx.log.append({
            "role": "system",
            "content": f"【Skill deactivated: {name}】owner Psyche {owner} 已停用。",
            "_skill_event": event,
        })
        results.append(name)
    return results
