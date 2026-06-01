"""三段式上下文管理器 — CacheContext。

实现 DeepSeek 前缀缓存优化的核心架构：

    Immutable Prefix  ─ 系统提示词、环境信息、安全模式（会话级固定，永不修改）
    Append-Only Log   ─ 用户/助手/工具调用/结果（只追加）
    Volatile Scratch  ─ 思考过程、计划状态（存内存，不发给API）

用法：
    ctx = CacheContext(prefix_msgs=[...], log_msgs=[...])
    api_msgs = ctx.send()               # 构建 API 消息，校验 prefix 完整性
    ctx.log.append({"role": "user", ...})  # 追加对话
    ctx.stats()                          # 三段统计
    session.save(ctx.all)               # prefix + log 用于持久化
"""

import hashlib as _hashlib
import json
import logging

logger = logging.getLogger(__name__)


def _compute_msg_hash(msgs: list[dict]) -> str:
    """计算消息列表的内容哈希（SHA-256 前 16 字符）。"""
    payload = json.dumps(
        [{"role": m["role"], "content": m.get("content", "")} for m in msgs],
        ensure_ascii=False,
        sort_keys=True,
    )
    # 清理 Windows 子进程管道可能产生的孤立代理字符
    safe = payload.encode("utf-8", errors="replace").decode("utf-8")
    return _hashlib.sha256(safe.encode()).hexdigest()[:16]


class PrefixIntegrityError(RuntimeError):
    """前缀哈希校验失败 — 不可变前缀被意外修改。"""
    pass


class CacheContext:
    """三段式上下文管理器。

    ┌──────────────────────┐
    │ IMMUTABLE PREFIX     │ ← 会话级固定，send() 时哈希校验
    ├──────────────────────┤
    │ APPEND-ONLY LOG      │ ← 只追加，send() 时跟在 prefix 后面
    ├──────────────────────┤
    │ VOLATILE SCRATCH     │ ← 纯内存，不参与 send()
    └──────────────────────┘
    """

    def __init__(self, prefix_msgs: list[dict] | None = None,
                 log_msgs: list[dict] | None = None):
        self.prefix: list[dict] = list(prefix_msgs or [])
        self.log: list[dict] = list(log_msgs or [])
        self.scratch: list[dict] = []
        self._prefix_hash: str = _compute_msg_hash(self.prefix)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def prefix_hash(self) -> str:
        """不可变前缀的哈希值（16 字符 hex）。"""
        return self._prefix_hash

    @property
    def all(self) -> list[dict]:
        """prefix + log（用于 session 持久化；save_session 会过滤 _volatile）。"""
        return self.prefix + self.log

    def stats(self) -> dict:
        """三段统计：各段消息数 + 估算 token。

        Returns:
            {
                "prefix": {"messages": N, "tokens": N},
                "log":    {"messages": N, "tokens": N, "volatile": N},
                "scratch": {"messages": N, "tokens": N},
                "total":  {"messages": N, "tokens": N},
            }
        """
        def _count(msgs):
            n = len(msgs)
            t = sum(len(m.get("content") or "") // 4 for m in msgs)
            return {"messages": n, "tokens": t}

        prefix = _count(self.prefix)
        log = _count(self.log)
        log["volatile"] = sum(1 for m in self.log if m.get("_volatile"))
        scratch = _count(self.scratch)

        return {
            "prefix": prefix,
            "log": log,
            "scratch": scratch,
            "total": {
                "messages": prefix["messages"] + log["messages"] + scratch["messages"],
                "tokens": prefix["tokens"] + log["tokens"] + scratch["tokens"],
            },
        }

    def last_user_content(self) -> str | None:
        """返回最后一条 user 消息的内容，没有则返回 None。"""
        for m in reversed(self.log):
            if m.get("role") == "user":
                return m.get("content")
        return None

    # ── 核心方法 ──────────────────────────────────────────

    def send(self, validate: bool = True) -> list[dict]:
        """构建发给 LLM API 的消息列表。

        策略（保障 DeepSeek 前缀缓存）：
          1. 不可变 prefix 系统消息排在 payload 最前面 → 缓存命中核心区
          2. _volatile 标记的 prefix 消息跳过（不应混入不可变前缀）
          3. log 中非 system 消息按追加顺序排后面 → 前缀持续命中
          4. log 中 system 消息（记忆/安全模式/摘要）放末尾 → 不影响缓存前缀
        Args:
            validate: 是否校验 prefix 完整性，默认 True。

        Returns:
            API-ready 消息列表。

        Raises:
            PrefixIntegrityError: validate=True 且 prefix 被意外修改。
        """
        # 1. 校验 prefix 完整性
        if validate:
            current = _compute_msg_hash(self.prefix)
            if current != self._prefix_hash:
                raise PrefixIntegrityError(
                    f"前缀哈希不匹配！预期 {self._prefix_hash}，实际 {current}。"
                    f"不可变 prefix 被意外修改。"
                )
        api: list[dict] = []

        # 2. immutable prefix — 跳过 _volatile 标记的消息（它们属于 scratch）
        for m in self.prefix:
            if m.get("_volatile"):
                continue
            api.append({"role": "system", "content": m.get("content", "")})

        # 3. log 中的非 system 消息（user/assistant/tool）—— 紧跟 prefix，享受缓存
        for m in self.log:
            if m.get("role") == "system":
                continue
            clean: dict = {"role": m["role"]}
            if "content" in m:
                clean["content"] = m.get("content")
            if m.get("tool_calls"):
                clean["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                clean["tool_call_id"] = m["tool_call_id"]
            api.append(clean)

        # 4. log 中的 system 消息（记忆/安全模式/摘要等）—— 放末尾，不影响对话缓存
        for m in self.log:
            if m.get("role") != "system":
                continue
            api.append({"role": "system", "content": m.get("content", "")})

        return api

    def clear_log(self) -> None:
        """清空 log，保持 prefix 不变。"""
        self.log.clear()

    def clear_scratch(self) -> None:
        """清空 scratch。"""
        self.scratch.clear()

    def rebuild_prefix(self, prefix_msgs: list[dict]) -> None:
        """重建 prefix（如 /clear 后重新注入环境上下文）。

        Args:
            prefix_msgs: 新的 prefix 消息列表。
        """
        self.prefix = list(prefix_msgs)
        self._prefix_hash = _compute_msg_hash(self.prefix)

    def append_to_prefix(self, msg: dict) -> None:
        """向 prefix 追加一条消息并重新计算 hash。

        谨慎使用 — 这会改变缓存前缀。仅用于 session 初始化阶段。
        """
        self.prefix.append(msg)
        self._prefix_hash = _compute_msg_hash(self.prefix)

    # ── 便利方法 ──────────────────────────────────────────

    def __len__(self) -> int:
        """消息总数（prefix + log + scratch）。"""
        return len(self.prefix) + len(self.log) + len(self.scratch)

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"CacheContext(prefix={s['prefix']['messages']}msgs/{s['prefix']['tokens']}tok, "
            f"log={s['log']['messages']}msgs/{s['log']['tokens']}tok, "
            f"scratch={s['scratch']['messages']}msgs/{s['scratch']['tokens']}tok)"
        )


# ── 向后兼容的包装函数（保持 llm/commands 的导入路径不变） ──

def compute_prefix_hash(messages: list[dict]) -> tuple[str, int, int]:
    """向后兼容：从扁平 messages 列表计算系统消息前缀哈希。

    Returns:
        (hex_hash, 字符数, token估算数)
    """
    system_msgs = [
        {"role": "system", "content": m.get("content", "")}
        for m in messages if m.get("role") == "system"
    ]
    h = _compute_msg_hash(system_msgs)
    chars = sum(len(m["content"]) for m in system_msgs)
    return h, chars, chars // 4
