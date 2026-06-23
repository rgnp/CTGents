"""System Context — 可刷新的上下文源管理。

借鉴 OpenCode 的 System Context 模式：
每条上下文是一个独立源（Source），有 baseline/update/removed 三个生命周期阶段。
reconcile() 在每轮 LLM 调用前运行，对比 snapshot 检测变更，只推送增量。

不破坏前缀缓存：变更写入 ctx.log append-only（非 volatile），不走挂尾。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from .cache_context import CacheContext


class Source:
    """一条可刷新的系统上下文源。

    Attributes:
        key: 稳定标识（如 "psyche/aesthetic-design"）
        snapshot: 用于检测变更的哈希值
        baseline: 首次注入时的消息 dict（None=无需 baseline）
        update: 变更时生成增量消息 (prev_hash, curr_hash) → dict | None
    """

    def __init__(
        self,
        key: str,
        snapshot: Any,
        *,
        baseline: dict | None = None,
        update: Callable[[str, str], dict | None] | None = None,
    ):
        self.key = key
        self._snapshot = snapshot
        self._snapshot_hash = _hash(snapshot)
        self._baseline = baseline
        self._update = update

    @property
    def snapshot_hash(self) -> str:
        return self._snapshot_hash

    def make_baseline(self) -> dict | None:
        return self._baseline

    def make_update(self, prev_hash: str) -> dict | None:
        if self._update and self._snapshot_hash != prev_hash:
            return self._update(prev_hash, self._snapshot_hash)
        return None


def _hash(value: Any) -> str:
    raw = str(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# 模块级注册表
_registry: dict[str, Source] = {}
# 上次 reconcile 时各 key 的 snapshot hash
_known: dict[str, str] = {}
# 已注入 baseline 的 key
_injected: set[str] = set()
# 等待推送的 removed 消息（unregister 时排队，reconcile 时推送）
_pending_removed: list[dict] = []


def register(source: Source) -> None:
    """注册一个上下文源。key 已存在时覆盖（重新加载）。"""
    _registry[source.key] = source


def unregister(key: str, removed_message: dict | None = None) -> None:
    """注销上下文源，可选附带移除通知消息。

    消息在下一轮 reconcile 时推送给 LLM。如果 source 定义时有 baseline，
    建议附带 removed_message 告知 LLM 该上下文不再可用。
    """
    _registry.pop(key, None)
    _known.pop(key, None)
    _injected.discard(key)
    if removed_message:
        _pending_removed.append(removed_message)


def reconcile(ctx: CacheContext) -> None:
    """扫描注册源，对比 snapshot，推送变更/移除通知。

    在每轮 LLM 调用前执行。三种变更：
    1. 新源（不在 _injected 中）→ 注入 baseline
    2. 已有源但 snapshot 变了 → 注入 update
    3. 有 pending removed → 注入移除通知

    所有注入 append 到 ctx.log（非 volatile，不走挂尾）。
    """
    # 推送 pending removed 消息
    while _pending_removed:
        ctx.log.append(_pending_removed.pop(0))

    # 检测新增和变更的源
    for key, source in _registry.items():
        current_hash = source.snapshot_hash

        if key not in _injected:
            baseline = source.make_baseline()
            if baseline:
                ctx.log.append(baseline)
            _injected.add(key)
            _known[key] = current_hash
        elif key in _known and _known[key] != current_hash:
            update = source.make_update(_known[key])
            if update:
                ctx.log.append(update)
            _known[key] = current_hash


def loaded_keys() -> set[str]:
    """返回当前已注册的 key 集合。"""
    return set(_registry.keys())


def reset() -> None:
    """清空所有注册信息（会话切换时调用）。"""
    _registry.clear()
    _known.clear()
    _injected.clear()
    _pending_removed.clear()
