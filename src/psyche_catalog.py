"""Psyche Catalog：manifest 发现、机械校验与依赖 DAG 解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .paths import CORE_ROOT, PSYCHE_ROOT, SKILLS_ROOT

PROJECT_ROOT = CORE_ROOT
DEFAULT_PSYCHE_ROOT = PSYCHE_ROOT
DEFAULT_SKILLS_ROOT = SKILLS_ROOT
VALID_KINDS = frozenset({"base", "domain", "subdomain", "mode"})
VALID_SCOPES = frozenset({"base", "session", "task"})


class PsycheCatalogError(ValueError):
    """Psyche manifest 或依赖图不合法。"""


@dataclass(frozen=True)
class PsycheSpec:
    """一个可加载 Psyche 的机器契约。"""

    id: str
    version: str
    kind: str
    manifest_path: Path
    core_path: Path
    requires: tuple[str, ...]
    scope_default: str
    summary: str
    judgment_delta: tuple[str, ...]
    skills: tuple[str, ...]
    exit_checks: tuple[str, ...]
    conflicts: tuple[str, ...] = ()


def _string_list(data: dict, key: str, manifest: Path) -> tuple[str, ...]:
    value = data.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PsycheCatalogError(f"{manifest}: {key} 必须是非空字符串列表")
    return tuple(item.strip() for item in value)


def _load_spec(manifest: Path) -> PsycheSpec:
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PsycheCatalogError(f"无法读取 {manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PsycheCatalogError(f"{manifest}: 顶层必须是映射")

    required = ("id", "version", "kind", "core", "scope_default", "summary")
    missing = [key for key in required if not str(raw.get(key, "")).strip()]
    if missing:
        raise PsycheCatalogError(f"{manifest}: 缺少字段 {', '.join(missing)}")

    psyche_id = str(raw["id"]).strip()
    version = str(raw["version"]).strip()
    kind = str(raw["kind"]).strip()
    scope = str(raw["scope_default"]).strip()
    if kind not in VALID_KINDS:
        raise PsycheCatalogError(f"{manifest}: kind={kind!r} 不在 {sorted(VALID_KINDS)}")
    if scope not in VALID_SCOPES:
        raise PsycheCatalogError(f"{manifest}: scope_default={scope!r} 不在 {sorted(VALID_SCOPES)}")
    if kind == "base" and scope != "base":
        raise PsycheCatalogError(f"{manifest}: base Psyche 的 scope_default 必须是 base")

    core_path = (manifest.parent / str(raw["core"])).resolve()
    try:
        core_path.relative_to(manifest.parent.resolve())
    except ValueError as exc:
        raise PsycheCatalogError(f"{manifest}: core 不能越出 Psyche 目录") from exc
    if not core_path.is_file():
        raise PsycheCatalogError(f"{manifest}: core 不存在: {core_path}")

    return PsycheSpec(
        id=psyche_id,
        version=version,
        kind=kind,
        manifest_path=manifest.resolve(),
        core_path=core_path,
        requires=_string_list(raw, "requires", manifest),
        scope_default=scope,
        summary=str(raw["summary"]).strip(),
        judgment_delta=_string_list(raw, "judgment_delta", manifest),
        skills=_string_list(raw, "skills", manifest),
        exit_checks=_string_list(raw, "exit_checks", manifest),
        conflicts=_string_list(raw, "conflicts", manifest),
    )


def load_catalog(
    psyche_root: Path | str = DEFAULT_PSYCHE_ROOT,
    skills_root: Path | str = DEFAULT_SKILLS_ROOT,
) -> dict[str, PsycheSpec]:
    """扫描并校验全部 manifest；任何坏条目都 fail-closed。"""
    root = Path(psyche_root)
    manifests = sorted(root.rglob("manifest.yaml")) if root.is_dir() else []
    if not manifests:
        raise PsycheCatalogError(f"Psyche Catalog 为空：{root}")

    catalog: dict[str, PsycheSpec] = {}
    for manifest in manifests:
        spec = _load_spec(manifest)
        if spec.id in catalog:
            raise PsycheCatalogError(
                f"Psyche id 重复: {spec.id}（{catalog[spec.id].manifest_path} / {manifest}）"
            )
        catalog[spec.id] = spec

    for spec in catalog.values():
        missing = [dep for dep in spec.requires if dep not in catalog]
        if missing:
            raise PsycheCatalogError(f"{spec.id}: 依赖不存在: {', '.join(missing)}")
        if spec.id in spec.requires:
            raise PsycheCatalogError(f"{spec.id}: 不能依赖自己")
        missing_conflicts = [name for name in spec.conflicts if name not in catalog]
        if missing_conflicts:
            raise PsycheCatalogError(f"{spec.id}: 冲突 Psyche 不存在: {', '.join(missing_conflicts)}")
        if spec.id in spec.conflicts:
            raise PsycheCatalogError(f"{spec.id}: 不能和自己冲突")
        for skill in spec.skills:
            if not (Path(skills_root) / skill / "SKILL.md").is_file():
                raise PsycheCatalogError(f"{spec.id}: Skill 不存在: {skill}")

    _validate_acyclic(catalog)
    for spec in catalog.values():
        asymmetric = [name for name in spec.conflicts if spec.id not in catalog[name].conflicts]
        if asymmetric:
            raise PsycheCatalogError(
                f"{spec.id}: conflicts 必须双向声明，缺少: "
                + ", ".join(f"{name} -> {spec.id}" for name in asymmetric)
            )
    return catalog


def _validate_acyclic(catalog: dict[str, PsycheSpec]) -> None:
    visiting: list[str] = []
    done: set[str] = set()

    def visit(psyche_id: str) -> None:
        if psyche_id in done:
            return
        if psyche_id in visiting:
            start = visiting.index(psyche_id)
            cycle = visiting[start:] + [psyche_id]
            raise PsycheCatalogError(f"Psyche 依赖成环: {' → '.join(cycle)}")
        visiting.append(psyche_id)
        for dep in catalog[psyche_id].requires:
            visit(dep)
        visiting.pop()
        done.add(psyche_id)

    for psyche_id in catalog:
        visit(psyche_id)


def resolve_load_order(
    target: str,
    catalog: dict[str, PsycheSpec],
    active: set[str] | None = None,
) -> list[PsycheSpec]:
    """返回尚未激活的依赖拓扑序，目标 Psyche 最后。"""
    if target not in catalog:
        raise PsycheCatalogError(f"找不到 psyche '{target}'。可用: {', '.join(sorted(catalog))}")
    active = active or set()
    ordered: list[PsycheSpec] = []
    seen: set[str] = set()

    def visit(psyche_id: str) -> None:
        if psyche_id in seen or psyche_id in active:
            return
        seen.add(psyche_id)
        spec = catalog[psyche_id]
        for dep in spec.requires:
            visit(dep)
        ordered.append(spec)

    visit(target)
    planned: set[str] = set(active)
    for spec in ordered:
        collisions = sorted(set(spec.conflicts) & planned)
        if collisions:
            raise PsycheCatalogError(
                f"{spec.id} 与 active/planned Psyche 冲突: {', '.join(collisions)}"
            )
        planned.add(spec.id)
    return ordered


def catalog_text(catalog: dict[str, PsycheSpec], query: str = "") -> str:
    """渲染紧凑能力目录；query 仅用于显式查询，不参与自动触发。"""
    query = query.strip().lower()
    specs = []
    for spec in catalog.values():
        haystack = " ".join((spec.id, spec.summary, *spec.judgment_delta)).lower()
        if not query or query in haystack:
            specs.append(spec)
    if not specs:
        return f"没有与「{query}」匹配的 Psyche。"
    lines = ["Psyche Catalog（用于补认知缺口，不按关键词自动加载）："]
    for spec in sorted(specs, key=lambda item: item.id):
        delta = "；".join(spec.judgment_delta[:2]) or "未声明判断增量"
        lines.append(f"- {spec.id} [{spec.scope_default}]：{spec.summary}｜{delta}")
    return "\n".join(lines)
