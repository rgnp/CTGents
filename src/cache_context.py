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
        # 循环控制信号：agent 调 task_done/need_user 时由 run_conversation 置位（取代
        # "不调工具=结束"的隐式判断）。续跑/主干据此决定停或继续。见 tools/control.py。
        self.control_signal: str | None = None
        self.control_payload: str | None = None

    # ── 属性 ──────────────────────────────────────────────

    @property
    def prefix_hash(self) -> str:
        """不可变前缀的哈希值（16 字符 hex）。"""
        return self._prefix_hash

    @property
    def all(self) -> list[dict]:
        """Prefix + log（用于 session 持久化；save_session 会过滤 _volatile）。"""
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
        """构建发给 LLM API 的消息列表 —— Reasonix 式纯追加（唯一模式）。

        prefix 段：不可变系统消息（会话级冻结、哈希锁死）。
        log 段：只追加对话（user/assistant/tool），丢弃 volatile system。
        尾段：游离态挂尾——阅后即焚，不进 self.log，利用近因效应聚焦当前任务步骤。
        前缀缓存不受影响（prefix + log 序列不变，尾段是临时追加的易失消息）。

        Args:
            validate: 是否校验 prefix 完整性，默认 True。

        Returns:
            API-ready 消息列表。

        Raises:
            PrefixIntegrityError: validate=True 且 prefix 被意外修改。
        """
        if validate:
            self._validate_prefix()
        api: list[dict] = []

        # immutable prefix（全部发送；_volatile 仅影响持久化过滤，不影响 API）
        for m in self.prefix:
            api.append({"role": "system", "content": m.get("content", "")})

        # prefix 之后纯追加：丢弃所有 volatile system 消息
        kept = [m for m in self.log
                if not (m.get("role") == "system" and m.get("_volatile"))]
        api.extend(self._repair_tool_pairing([self._clean_log_msg(m) for m in kept]))

        # ── 游离态挂尾注入（阅后即焚，不进 self.log）──
        try:
            from .tasks import read_current_active_step
            _active = read_current_active_step()
            if _active:
                api.append({
                    "role": "system",
                    "content": (
                        "【系统最高指令 / 当前操作台】\n"
                        "无论上方的历史对话多么冗长，你当前必须且只能聚焦于以下任务切片：\n"
                        f"👉 当前执行步骤：\n{_active}\n\n"
                        "🛡️ 核心纪律强制刷新：\n"
                        "1. 你无权修改 IMMUTABLE_FILES（不可变安全核）。\n"
                        "2. 写代码（edit_file_lines）之前必须确保你已重新阅读了该文件获取最新行号。\n"
                        "3. 请立即执行当前步骤，若遇到任何审计拦截（Audit），优先自我纠正！"
                    ),
                })
        except Exception:
            pass

        return api

    def _validate_prefix(self) -> None:
        """校验前缀完整性。不匹配则抛 PrefixIntegrityError。"""
        current = _compute_msg_hash(self.prefix)
        if current != self._prefix_hash:
            raise PrefixIntegrityError(
                f"前缀哈希不匹配！预期 {self._prefix_hash}，实际 {current}。"
                f"不可变 prefix 被意外修改。"
            )

    @staticmethod
    def _clean_log_msg(m: dict) -> dict:
        """清洗 log 中的一条非 system 消息，只保留 API 需要的字段。"""
        clean: dict = {"role": m["role"]}
        if "content" in m:
            clean["content"] = m.get("content")
        if m.get("tool_calls"):
            clean["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            clean["tool_call_id"] = m["tool_call_id"]
        return clean

    @staticmethod
    def _repair_tool_pairing(msgs: list[dict]) -> list[dict]:
        """焊死 OpenAI/DeepSeek 协议不变量，防 400 卡死整个会话。

        协议要求：带 tool_calls 的 assistant 消息后必须紧跟每个 tool_call_id
        对应的 tool 结果消息。工具执行中途被中断（KeyboardInterrupt 不被 llm.py
        的 except Exception 捕获）、异常、或进程崩溃，都可能在 log 里留下"光杆
        tool_calls"（缺结果），落盘后每轮重发都 400、重启加载也照炸——会话彻底卡死。

        这里在唯一咽喉 send() 处兜底：缺失的 tool 结果补占位消息（紧跟 assistant
        之后）。健康 log 不增不删、字节不变（零缓存影响），只有坏 log 被修复。
        （只补缺失结果，不动孤儿 tool 消息——后者是另一类、非中断所致，不在此处臆测处理。）
        """
        has_result = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
        out: list[dict] = []
        for m in msgs:
            out.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tcid = tc.get("id")
                    if tcid and tcid not in has_result:
                        out.append({
                            "role": "tool",
                            "tool_call_id": tcid,
                            "content": json.dumps(
                                {"error": "工具结果缺失（中断/异常/崩溃），已占位"},
                                ensure_ascii=False),
                        })
        return out

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
