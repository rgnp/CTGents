"""Psyche 上下文注入桥。

把 psyche 核心认知框架注入到对话上下文的 log 末尾（append，不插队），作为系统消息
发送给 API。停用时追加 deactivate 事件，历史 core 不删除。

⚠️ 必须 append 到 log 末尾，不能 insert(0)：insert(0) 会让此前所有 log 消息的字节
偏移，等同 docs/cache-design.md 记录的历史"缓存毒药"问题（当年 insert(0, env_message)
致 DeepSeek 前缀缓存 100% 失效，三段式重构才修好）。mid-conversation 加载 psyche 时
若插到最前面，会让此前积累的整段对话瞬间失去缓存命中——append 到尾部才是唯一的
cache-safe 写法（也更符合"尾部靠 recency 影响行为"的经验）。

用法（通过 commands.py 的 /psyche 指令触发，不在子进程跑）：
    inject_psyche(ctx, "software-development")   # 读核心 + 注入
    remove_psyche(ctx, "software-development")   # append-only 停用
    status_text(ctx)                              # 当前活跃 psyche
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import PSYCHE_ROOT
from .psyche_catalog import (
    DEFAULT_SKILLS_ROOT,
    PsycheCatalogError,
    PsycheSpec,
    catalog_text,
    load_catalog,
    resolve_load_order,
)

if TYPE_CHECKING:
    from .cache_context import CacheContext

_PSYCHE_ROOT = PSYCHE_ROOT


def _catalog() -> dict[str, PsycheSpec]:
    return load_catalog(Path(_PSYCHE_ROOT), DEFAULT_SKILLS_ROOT)


def _rebuild_stack(ctx: CacheContext) -> dict[str, dict]:
    """从持久化事件归约 Active Stack；兼容旧 `_psyche_meta` 会话。"""
    active: dict[str, dict] = {}
    for msg in ctx.log:
        event = msg.get("_psyche_event")
        if event:
            psyche_id = event.get("id") or event.get("name")
            if not psyche_id:
                continue
            if event.get("type") == "deactivate":
                active.pop(psyche_id, None)
            elif event.get("type") == "activate":
                active[psyche_id] = dict(event)
            continue
        meta = msg.get("_psyche_meta")
        if meta and meta.get("name"):
            # 旧会话没有停用事件；按原行为视为 session scope 激活。
            active[meta["name"]] = {
                "type": "activate",
                "id": meta["name"],
                "name": meta["name"],
                "version": meta.get("version", "?"),
                "scope": meta.get("scope", "session"),
                "source": meta.get("source", "legacy"),
                "activation_id": meta.get("activation_id", f"legacy-{meta['name']}"),
            }
    ctx.psyche_stack = active
    ctx._psyche_stack_synced = True
    return active


def _active_stack(ctx: CacheContext) -> dict[str, dict]:
    if not getattr(ctx, "_psyche_stack_synced", False):
        return _rebuild_stack(ctx)
    return ctx.psyche_stack


def loaded_psyches_in_log(ctx: CacheContext) -> list[dict]:
    """返回当前 Active Psyche Stack（保留旧函数名兼容调用方）。"""
    return [dict(meta) for meta in _active_stack(ctx).values()]


def resync_system_context(ctx: CacheContext) -> None:
    """切会话（/load）后重新同步 system_context 注册表，使其匹配 ctx.log 里实际的 psyche。

    system_context._registry 是模块级全局、不属于某个会话。/load 直接用 ctx.log.extend()
    灌入磁盘消息，不经过 inject_psyche，注册表不会自动更新——不重置会残留上一个会话的
    psyche key（self 工具的自知状态读 loaded_keys()，因此报出假数据）；只 reset() 不重新
    登记，又会丢失新加载会话里本来就有的 psyche。两步都要做。
    """
    from . import system_context

    active = _rebuild_stack(ctx)
    system_context.reset()
    for meta in active.values():
        system_context.register(system_context.Source(
            key=f"psyche/{meta.get('id') or meta.get('name')}",
            snapshot=meta.get("version", "?"),
        ))


def inject_psyche(
    ctx: CacheContext,
    name: str,
    *,
    scope: str | None = None,
    source: str = "user",
    reason: str = "",
) -> str:
    """原子解析依赖并把 Psyche 激活事件 append 到 log。

    Args:
        ctx: CacheContext 实例
        name: psyche 名称（如 "software-development" 或 "aesthetic-design"）
        scope: 生命周期，None 时读取 manifest 默认值
        source: 激活来源（base/user/agent/dependency）
        reason: 本次加载要补的具体认知缺口

    Returns:
        提示消息
    """
    try:
        catalog = _catalog()
        target = catalog[name]
    except KeyError:
        return f"❌ 找不到 psyche '{name}'。可用: {_list_available()}"
    except PsycheCatalogError as exc:
        return f"❌ Psyche Catalog 无效: {exc}"

    active = _active_stack(ctx)
    if name in active:
        return f"⚠️ {name} 已加载，无需重复注入。"

    target_scope = scope or target.scope_default
    if source == "agent":
        target_scope = "task"  # Agent 自主加载只允许低摩擦、可自动停用的 task scope。
    if target.kind == "base":
        target_scope = "base"
    if target_scope not in {"base", "session", "task"}:
        return f"❌ scope 必须是 base/session/task，收到: {target_scope}"

    try:
        order = resolve_load_order(name, catalog, set(active))
        # 原子性：先把所有 core 读完；任何一个失败都不写 log/stack。
        prepared = [(spec, spec.core_path.read_text(encoding="utf-8")) for spec in order]
    except (PsycheCatalogError, OSError) as exc:
        return f"❌ Psyche 加载事务失败（零写入）: {exc}"

    from .system_context import Source
    from .system_context import register as _register_source

    activated: list[str] = []
    for spec, core_text in prepared:
        activation_id = uuid.uuid4().hex[:12]
        event_scope = "base" if spec.kind == "base" else target_scope
        event_source = source if spec.id == name else ("base" if spec.kind == "base" else "dependency")
        event = {
            "type": "activate",
            "id": spec.id,
            "name": spec.id,
            "version": spec.version,
            "content_hash": hashlib.sha256(core_text.encode("utf-8")).hexdigest()[:16],
            "scope": event_scope,
            "source": event_source,
            "reason": reason if spec.id == name else f"dependency of {name}",
            "activation_id": activation_id,
            "requires": list(spec.requires),
            "skills": list(spec.skills),
        }
        ctx.log.append({
            "role": "system",
            "content": (
                f"【Psyche activated: {spec.id} v{spec.version} | scope={event_scope}】\n\n"
                f"{core_text}\n\n"
                "【激活后协议】重新审视当前问题，指出此前遗漏的判断维度；"
                "需要流程化执行时，只调用本 Psyche 声明的 Skill。"
            ),
            "_psyche_event": event,
            # 兼容旧 UI/压缩标记；Active 状态只读 _psyche_event/psyche_stack。
            "_psyche_meta": {
                "name": spec.id,
                "version": spec.version,
                "scope": event_scope,
                "source": event_source,
                "activation_id": activation_id,
            },
        })
        active[spec.id] = event
        _register_source(Source(key=f"psyche/{spec.id}", snapshot=spec.version))
        activated.append(spec.id)

    return (
        f"✅ 已激活 Psyche：{' → '.join(activated)}（目标 {name}，scope={target_scope}）。"
        "依赖已原子解析，事件追加在对话末尾。"
    )


def remove_psyche(ctx: CacheContext, name: str, *, source: str = "user") -> str:
    """Append deactivate 事件；不删除历史消息、不破坏缓存。"""
    active = _active_stack(ctx)
    if name == _BASE_PSYCHE:
        return "❌ general 是常驻认知内核，不可卸载。"
    if name not in active:
        return f"⚠️ 未找到已激活的 psyche「{name}」。可用 /psyche status 查看。"

    try:
        catalog = _catalog()
        dependents = sorted(
            psyche_id for psyche_id in active
            if psyche_id in catalog and name in catalog[psyche_id].requires
        )
    except PsycheCatalogError as exc:
        return f"❌ Psyche Catalog 无效: {exc}"
    if dependents:
        return f"❌ 不能停用 {name}：仍被 active Psyche 依赖：{', '.join(dependents)}"

    from .skill_bridge import deactivate_skills_for_owner

    deactivate_skills_for_owner(ctx, name)
    previous = active.pop(name)
    event = {
        "type": "deactivate",
        "id": name,
        "name": name,
        "version": previous.get("version", "?"),
        "scope": previous.get("scope", "session"),
        "source": source,
        "activation_id": previous.get("activation_id", "?"),
    }
    ctx.log.append({
        "role": "system",
        "content": f"【Psyche deactivated: {name}】此后不再按该 Psyche 判断。",
        "_psyche_event": event,
    })

    from .system_context import unregister as _unregister_source

    _unregister_source(f"psyche/{name}")
    return f"✅ 已停用 psyche「{name}」（append-only，历史消息保留）。"


def status_text(ctx: CacheContext) -> str:
    """返回当前 psyche 加载状态。"""
    loaded = loaded_psyches_in_log(ctx)
    if not loaded:
        return "当前无加载的 psyche。"
    lines = [f"Active Psyche Stack（{len(loaded)}）："]
    for meta in loaded:
        name = meta.get("id") or meta.get("name")
        lines.append(
            f"  🧬 {name}  v{meta.get('version', '?')}  "
            f"scope={meta.get('scope', '?')} source={meta.get('source', '?')}"
        )
    return "\n".join(lines)


# ── 内部 ──

def _find_core_file(name: str) -> str | None:
    """按 manifest id 定位 core；目录深度不再参与依赖判断。"""
    try:
        spec = _catalog().get(name)
    except PsycheCatalogError:
        return None
    return str(spec.core_path) if spec else None


# 通用人格：不分领域、每会话常驻注入（会话首轮 log 为空时 append，等效于最前面），领域 psyche 在其上叠加。
# 实验（2026-06-24）：AGENTS.md 的 <bias>+<tone> 改写成第一人称人格搬到这里，从前缀删除——
# 测"通用姿态写成人格 vs 写成前缀规则"哪个真改行为（领域 psyche 起效是形式还是内容的隔离实验）。
_BASE_PSYCHE = "general"


def ensure_base_psyche(ctx: CacheContext) -> str | None:
    """常驻基础人格：未加载则注入 general psyche。返回注入提示或 None。

    幂等（已加载/文件不存在/关闭开关 → None）。CTG_BASE_PSYCHE=0 可关。
    删除内置 general Psyche 资源也能禁用（catalog 找不到即 None）。
    """
    if os.environ.get("CTG_BASE_PSYCHE", "1") == "0":
        return None
    if not _find_core_file(_BASE_PSYCHE):
        return None
    if any(meta.get("name") == _BASE_PSYCHE for meta in loaded_psyches_in_log(ctx)):
        return None
    return inject_psyche(ctx, _BASE_PSYCHE, scope="base", source="base", reason="session bootstrap")


def _list_available() -> str:
    """列出 manifest Catalog 中的全部 Psyche，包括任意深度子 Psyche。"""
    try:
        names = sorted(_catalog())
    except PsycheCatalogError as exc:
        return f"（Catalog 无效: {exc}）"
    return ", ".join(names) if names else "（无可用 psyche）"


def catalog_status_text(ctx: CacheContext, query: str = "") -> str:
    """Catalog + active 标记，供命令和模型显式查询。"""
    try:
        catalog = _catalog()
    except PsycheCatalogError as exc:
        return f"❌ Psyche Catalog 无效: {exc}"
    active = set(_active_stack(ctx))
    text = catalog_text(catalog, query)
    lines = []
    for line in text.splitlines():
        if line.startswith("- "):
            psyche_id = line[2:].split(" ", 1)[0]
            marker = "active" if psyche_id in active else "available"
            line = f"- [{marker}] {line[2:]}"
        lines.append(line)
    return "\n".join(lines)


def exit_checks_for_active(ctx: CacheContext) -> list[str]:
    """返回当前 Stack 的具体退出检查，替代空泛自律提醒。"""
    try:
        catalog = _catalog()
    except PsycheCatalogError:
        return []
    checks: list[str] = []
    for psyche_id in _active_stack(ctx):
        spec = catalog.get(psyche_id)
        if not spec:
            continue
        checks.extend(f"[{psyche_id}] {check}" for check in spec.exit_checks)
    return checks


def deactivate_scope(ctx: CacheContext, scope: str) -> list[str]:
    """按反向加载顺序停用指定 scope；task_done 用于自动收尾。"""
    if not hasattr(ctx, "log"):
        return []

    active = _active_stack(ctx)
    targets = [psyche_id for psyche_id, meta in active.items() if meta.get("scope") == scope]
    results = []
    for psyche_id in reversed(targets):
        results.append(remove_psyche(ctx, psyche_id, source="system"))
    return results
