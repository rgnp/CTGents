"""Psyche 上下文注入桥。

把 psyche 核心认知框架注入到对话上下文的 log 中（position 0，紧接 prefix 之后），
作为固定系统消息发送给 API。卸载时移除。

用法（通过 commands.py 的 /psyche 指令触发，不在子进程跑）：
    inject_psyche(ctx, "software-development")   # 读核心 + 注入
    remove_psyche(ctx, "software-development")   # 从 ctx.log 移除
    status_text(ctx)                              # 当前活跃 psyche
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache_context import CacheContext

_PSYCHE_ROOT = os.path.join(os.path.dirname(__file__), "..", "psyche")


def loaded_psyches_in_log(ctx: CacheContext) -> list[dict]:
    """扫描 ctx.log，返回所有已注入的 psyche 清单。"""
    result = []
    for msg in ctx.log:
        meta = msg.get("_psyche_meta")
        if meta:
            result.append(meta)
    return result


def inject_psyche(ctx: CacheContext, name: str) -> str:
    """读取 psyche 核心文件，注入 ctx.log position 0。

    Args:
        ctx: CacheContext 实例
        name: psyche 名称（如 "software-development" 或 "aesthetic-design"）

    Returns:
        提示消息
    """
    existing = loaded_psyches_in_log(ctx)
    for meta in existing:
        if meta.get("name") == name:
            return f"⚠️ {name} 已加载，无需重复注入。"

    core_path = _find_core_file(name)
    if not core_path:
        return f"❌ 找不到 psyche '{name}'。可用: {_list_available()}"

    try:
        with open(core_path, encoding="utf-8") as f:
            core_text = f.read()
    except Exception as e:
        return f"❌ 读取核心文件失败: {e}"

    version = _extract_meta(core_text, "版本")
    coverage = _extract_meta(core_text, "覆盖精度")

    # ── 子 Psyche 父依赖检查 ──
    parent = _check_parent_dependency(core_path, existing)
    if parent:
        return (
            f"❌ 子 Psyche「{name}」的父 Psyche「{parent}」未加载。\n"
            f"请先加载父 Psyche: /psyche load {parent}"
        )

    system_msg = {
        "role": "system",
        "content": f"【Psyche: {name} v{version or '?'} | {coverage or ''}】\n\n{core_text}",
        "_psyche_meta": {
            "name": name,
            "version": version or "?",
            "coverage": coverage or "",
        },
    }


    # ── 注册 system context source（生命周期管理） ──
    from .system_context import Source
    from .system_context import register as _register_source
    _register_source(Source(
        key=f"psyche/{name}",
        snapshot=version or "?",
    ))


    ctx.log.insert(0, system_msg)

    # ── 自动加载子 Psyche（父加载时自动带上常用的子） ──
    _auto_load_subs(ctx, name)

    return (
        f"✅ 已注入 psyche「{name}」v{version or '?'}"
        f"（{coverage or '覆盖精度未知'}）。"
        f"位置固定，不影响前缀缓存。"
    )



# ── 自动加载映射：父 Psyche → [子 Psyche 列表]
_AUTO_LOAD_SUBS: dict[str, list[str]] = {
    "psyche-building": ["learning-method"],
}


def _auto_load_subs(ctx: CacheContext, name: str) -> None:
    """父 Psyche 加载后自动注入其常用子 Psyche。

    只在子 Psyche 尚未加载时注入，不重复加载。
    """
    subs = _AUTO_LOAD_SUBS.get(name)
    if not subs:
        return
    for sub_name in subs:
        existing = loaded_psyches_in_log(ctx)
        if any(meta.get("name") == sub_name for meta in existing):
            continue
        inject_psyche(ctx, sub_name)



def _auto_remove_subs(ctx: CacheContext, name: str) -> None:
    """父 Psyche 卸载时自动移除其自动加载的子 Psyche。"""
    subs = _AUTO_LOAD_SUBS.get(name)
    if not subs:
        return
    for sub_name in subs:
        remove_psyche(ctx, sub_name)


def remove_psyche(ctx: CacheContext, name: str) -> str:
    """从 ctx.log 中移除指定 psyche 的系统消息。"""
    before = len(ctx.log)
    ctx.log[:] = [
        msg for msg in ctx.log
        if not (msg.get("_psyche_meta") and msg["_psyche_meta"].get("name") == name)
    ]
    removed = before - len(ctx.log)
    if removed > 0:
        # ── 注销 system context source + 推送移除通知 ──
        from .system_context import unregister as _unregister_source
        _unregister_source(
            f"psyche/{name}",
            removed_message={
                "role": "system",
                "content": f"【Psyche 已卸载: {name}】不再需要遵守其准则。",
                "_system_context": f"psyche/{name}",
            },
        )
        # ── 自动移除子 Psyche ──
        _auto_remove_subs(ctx, name)
        return f"✅ 已卸载 psyche「{name}」（移除了 {removed} 条系统消息，自律检查已更新）。"
    return f"⚠️ 未找到已加载的 psyche「{name}」。可用 /psyche list 查看。"


def status_text(ctx: CacheContext) -> str:
    """返回当前 psyche 加载状态。"""
    loaded = loaded_psyches_in_log(ctx)
    if not loaded:
        return "当前无加载的 psyche。"
    lines = [f"已加载 {len(loaded)} 个 psyche："]
    for meta in loaded:
        lines.append(f"  🧬 {meta['name']}  v{meta.get('version', '?')}  {meta.get('coverage', '')}")
    return "\n".join(lines)


# ── 内部 ──

_PSYCHE_SUB = os.path.join(_PSYCHE_ROOT, "software-development", "sub")


def _find_core_file(name: str) -> str | None:
    """在 psyche 目录树中查找核心文件。

    支持两级子 Psyche：
    1. psyche/{name}/核心/{name}-core.md（顶层）
    2. psyche/software-development/sub/{name}/核心/{name}-core.md（一级子）
    3. psyche/software-development/sub/{父}/sub/{name}/核心/{name}-core.md（二级子）
    """
    candidates = [
        os.path.join(_PSYCHE_ROOT, name, "核心", f"{name}-core.md"),
        os.path.join(_PSYCHE_SUB, name, "核心", f"{name}-core.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    # 二级子 Psyche：遍历所有一级子目录下的 sub/
    for parent_dir in os.listdir(_PSYCHE_SUB):
        sub_dir = os.path.join(_PSYCHE_SUB, parent_dir, "sub", name)
        core_path = os.path.join(sub_dir, "核心", f"{name}-core.md")
        if os.path.isfile(core_path):
            return core_path
    return None


def _extract_meta(text: str, field: str) -> str:
    """从核心文件的 meta 块提取字段值。"""
    m = re.search(rf"^>\s*{field}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""

def _check_parent_dependency(core_path: str, loaded: list[dict]) -> str | None:
    """检测子 Psyche 的父依赖是否已加载。

    子 Psyche 的 core 文件在 psyche/software-development/sub/{name}/ 下。
    只有子 Psyche 需要父依赖检查；顶层 Psyche 不检查。

    Returns:
        None = 无父依赖 / 父依赖已满足
        str = 缺失的父 Psyche 名称
    """
    # 判断是否是子 Psyche：core 路径包含 /sub/
    path_normalized = core_path.replace("\\", "/")
    if "/sub/" not in path_normalized:
        return None  # 顶层 psyche，无需父依赖

    # 从路径推断父 Psyche 名称（/sub/ 前的目录名）
    parent_name = os.path.basename(path_normalized.split("/sub/")[0])

    for meta in loaded:
        if meta.get("name") == parent_name:
            return None  # 父已加载
    return parent_name



def _list_available() -> str:
    """列出所有可用的 psyche。"""
    names = []
    if os.path.isdir(_PSYCHE_ROOT):
        for d in os.listdir(_PSYCHE_ROOT):
            core_dir = os.path.join(_PSYCHE_ROOT, d, "核心")
            if os.path.isdir(core_dir) and any(f.endswith("-core.md") for f in os.listdir(core_dir)):
                names.append(d)
    sub_root = os.path.join(_PSYCHE_ROOT, "software-development", "sub")
    if os.path.isdir(sub_root):
        for d in os.listdir(sub_root):
            core_dir = os.path.join(sub_root, d, "核心")
            if os.path.isdir(core_dir) and any(f.endswith("-core.md") for f in os.listdir(core_dir)):
                names.append(d)
    if not names:
        return "（无可用 psyche）"
    return ", ".join(sorted(names))
