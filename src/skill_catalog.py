"""Skill Catalog：发现、校验并按轴装配项目内 Skill。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .paths import CORE_ROOT, SKILLS_ROOT

PROJECT_ROOT = CORE_ROOT
DEFAULT_SKILLS_ROOT = SKILLS_ROOT


class SkillCatalogError(ValueError):
    """Skill manifest 或资源结构不合法。"""


@dataclass(frozen=True)
class SkillAxis:
    id: str
    values: tuple[str, ...]
    default: str


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    root: Path
    instructions_path: Path
    axes: tuple[SkillAxis, ...]
    always_load: tuple[Path, ...]


def _safe_resource(root: Path, relative: str, *, required: bool = True) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SkillCatalogError(f"Skill 资源不能越出目录: {relative}") from exc
    if required and not path.is_file():
        raise SkillCatalogError(f"Skill 资源不存在: {path}")
    return path


def _parse_axis(raw: object, manifest: Path) -> SkillAxis:
    if not isinstance(raw, dict) or not str(raw.get("id", "")).strip():
        raise SkillCatalogError(f"{manifest}: axis 必须包含 id")
    axis_id = str(raw["id"]).strip()
    raw_values = raw.get("values", [])
    if not isinstance(raw_values, list) or not raw_values:
        raise SkillCatalogError(f"{manifest}: axis {axis_id} 必须声明 values")
    values = []
    for item in raw_values:
        value = item.get("id") if isinstance(item, dict) else item
        if not isinstance(value, str) or not value.strip():
            raise SkillCatalogError(f"{manifest}: axis {axis_id} value 无效")
        values.append(value.strip())
    default = str(raw.get("default", values[0])).strip()
    if default not in values:
        raise SkillCatalogError(f"{manifest}: axis {axis_id} default={default!r} 不在 values 中")
    return SkillAxis(axis_id, tuple(values), default)


def load_skill_catalog(skills_root: Path | str = DEFAULT_SKILLS_ROOT) -> dict[str, SkillSpec]:
    """读取全部 Skill manifest；坏条目 fail-closed。"""
    root = Path(skills_root)
    manifests = sorted(root.glob("*/manifest.yaml")) if root.is_dir() else []
    catalog: dict[str, SkillSpec] = {}
    for manifest in manifests:
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SkillCatalogError(f"无法读取 {manifest}: {exc}") from exc
        if not isinstance(raw, dict):
            raise SkillCatalogError(f"{manifest}: 顶层必须是映射")
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        if not name or not version:
            raise SkillCatalogError(f"{manifest}: 缺少 name/version")
        if name in catalog:
            raise SkillCatalogError(f"Skill name 重复: {name}")
        skill_root = manifest.parent.resolve()
        instructions = _safe_resource(skill_root, "SKILL.md")
        axes_raw = raw.get("axes", []) or []
        if not isinstance(axes_raw, list):
            raise SkillCatalogError(f"{manifest}: axes 必须是列表")
        axes = tuple(_parse_axis(axis, manifest) for axis in axes_raw)
        if len({axis.id for axis in axes}) != len(axes):
            raise SkillCatalogError(f"{manifest}: axis id 重复")
        always_raw = raw.get("always_load", []) or []
        if not isinstance(always_raw, list) or not all(isinstance(x, str) for x in always_raw):
            raise SkillCatalogError(f"{manifest}: always_load 必须是字符串列表")
        always_load = tuple(_safe_resource(skill_root, item) for item in always_raw)
        catalog[name] = SkillSpec(name, version, skill_root, instructions, axes, always_load)
    return catalog


def render_skill(spec: SkillSpec, axes: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """校验轴并装配 SKILL.md、always_load 与命中的 convention fragment。"""
    requested = dict(axes or {})
    known = {axis.id for axis in spec.axes}
    unknown = sorted(set(requested) - known)
    if unknown:
        raise SkillCatalogError(f"{spec.name}: 未知轴: {', '.join(unknown)}")

    selected: dict[str, str] = {}
    fragments: list[Path] = []
    for axis in spec.axes:
        value = requested.get(axis.id, axis.default)
        if value not in axis.values:
            raise SkillCatalogError(
                f"{spec.name}: {axis.id}={value!r} 无效，可用: {', '.join(axis.values)}"
            )
        selected[axis.id] = value
        fragment = _safe_resource(
            spec.root, f"static/fragments/{axis.id}/{value}.md", required=False,
        )
        if fragment.is_file():
            fragments.append(fragment)

    paths = (spec.instructions_path, *spec.always_load, *fragments)
    sections = [path.read_text(encoding="utf-8").strip() for path in paths]
    return "\n\n---\n\n".join(section for section in sections if section), selected
