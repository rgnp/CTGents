"""Psyche 上下文注入桥。

把 psyche 核心认知框架注入到对话上下文的 log 末尾（append，不插队），作为系统消息
发送给 API。卸载时移除。

⚠️ 必须 append 到 log 末尾，不能 insert(0)：insert(0) 会让此前所有 log 消息的字节
偏移，等同 docs/cache-design.md 记录的历史"缓存毒药"问题（当年 insert(0, env_message)
致 DeepSeek 前缀缓存 100% 失效，三段式重构才修好）。mid-conversation 加载 psyche 时
若插到最前面，会让此前积累的整段对话瞬间失去缓存命中——append 到尾部才是唯一的
cache-safe 写法（也更符合"尾部靠 recency 影响行为"的经验）。

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


def resync_system_context(ctx: CacheContext) -> None:
    """切会话（/load）后重新同步 system_context 注册表，使其匹配 ctx.log 里实际的 psyche。

    system_context._registry 是模块级全局、不属于某个会话。/load 直接用 ctx.log.extend()
    灌入磁盘消息，不经过 inject_psyche，注册表不会自动更新——不重置会残留上一个会话的
    psyche key（self 工具的自知状态读 loaded_keys()，因此报出假数据）；只 reset() 不重新
    登记，又会丢失新加载会话里本来就有的 psyche。两步都要做。
    """
    from . import system_context
    system_context.reset()
    for meta in loaded_psyches_in_log(ctx):
        system_context.register(system_context.Source(
            key=f"psyche/{meta.get('name')}",
            snapshot=meta.get("version", "?"),
        ))


def inject_psyche(ctx: CacheContext, name: str) -> str:
    """读取 psyche 核心文件，append 到 ctx.log 末尾。

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


    ctx.log.append(system_msg)

    return (
        f"✅ 已注入 psyche「{name}」v{version or '?'}"
        f"（{coverage or '覆盖精度未知'}）。"
        f"追加在对话末尾，不破坏 append-only 缓存。"
    )


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



def _top_level_psyche_names() -> list[str]:
    """顶层领域 psyche 名（有核心文件的目录）。子 psyche 不参与自动加载（需父依赖）。"""
    names = []
    if os.path.isdir(_PSYCHE_ROOT):
        for d in os.listdir(_PSYCHE_ROOT):
            core_dir = os.path.join(_PSYCHE_ROOT, d, "核心")
            if os.path.isdir(core_dir) and any(f.endswith("-core.md") for f in os.listdir(core_dir)):
                names.append(d)
    return names


# 通用人格：不分领域、每会话常驻注入（会话首轮 log 为空时 append，等效于最前面），领域 psyche 在其上叠加。
# 实验（2026-06-24）：AGENTS.md 的 <bias>+<tone> 改写成第一人称人格搬到这里，从前缀删除——
# 测"通用姿态写成人格 vs 写成前缀规则"哪个真改行为（领域 psyche 起效是形式还是内容的隔离实验）。
_BASE_PSYCHE = "general"


def ensure_base_psyche(ctx: CacheContext) -> str | None:
    """常驻基础人格：未加载则注入 general psyche。返回注入提示或 None。

    幂等（已加载/文件不存在/关闭开关 → None）。CTG_BASE_PSYCHE=0 可关。
    删掉 psyche/general/ 目录也能禁用（_find_core_file 找不到即 None）。
    """
    if os.environ.get("CTG_BASE_PSYCHE", "1") == "0":
        return None
    if not _find_core_file(_BASE_PSYCHE):
        return None
    if any(meta.get("name") == _BASE_PSYCHE for meta in loaded_psyches_in_log(ctx)):
        return None
    return inject_psyche(ctx, _BASE_PSYCHE)


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
