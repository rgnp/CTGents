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

from __future__ import annotations

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


def _stub_tool_content(content: str, tool_call_id: str = "") -> str:
    """把陈旧大工具结果折成一行：保留首行信号 + 原长度 + 真实取回指引。

    首行常含关键信号（"已写入: path" / "退出码:..." / read 的文件头），留它让
    模型知道这步干了啥；原文不丢（在 self.log 里、随会话落盘）。需要原文时调
    fetch_tool_result(tool_call_id) 取回——folding 只动发送副本、原文一直在 log。
    """
    head = content.split("\n", 1)[0].strip()[:160]
    hint = (f'需要原文调 fetch_tool_result("{tool_call_id}")'
            if tool_call_id else "原文在会话存档")
    return f"{head} … 〔旧工具结果已折叠·原 {len(content)} 字·{hint}〕"


class PrefixIntegrityError(RuntimeError):
    """前缀哈希校验失败 — 不可变前缀被意外修改。"""

    pass


# ── 行为牙：思考牙 + 证据牙 ──
# 历史：5a96363（思考牙）/ b1316f5（证据牙）曾以 strip-then-append 挂 ctx.log 尾实现，
# 6-16 一度搬进前缀（对根深蒂固默认≈零效果，此前已实验验证过），随后连前缀那份也在
# AGENTS.md 精简过程中一并丢失——两颗牙实际已经消失。现在改走 send() 里已有的游离态
# 挂尾（同「当前任务步骤」提醒同款写法）：从不进 self.log，没有可剔除的旧消息，不会
# 重蹈 2026-06-18 那次"strip 打乱字节偏移"的缓存崩溃（prefix + log 逐字节不变）。
_THINKING_NUDGE = (
    "[提醒] 检索 / recall / 读到的内容是线索，不是答案。"
    "问方向 / 取舍 / \"怎么看\"时，先想清楚，给出你的判断 + 理由 + 你会怎么做，"
    "别把搜到的摆出来让用户挑；问事实就直接答、不必长。"
)

_EVIDENCE_NUDGE = (
    "[提醒] 下结论前先核证据够不够：这个判断依赖哪些条件？我这一轮真读/grep/搜过，"
    "还是凭印象？条件不全就先补（read/grep/search）；补不全就把信心收住、明说还差什么——"
    "别拿不全的输入给满分结论。"
)


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
        # send() 是否把 tasks/current.md 活动步骤挂尾进 payload。主会话 True；
        # delegate worker 等隔离 ctx 置 False——否则主 agent 的任务切片会以
        # "系统最高指令"泄进 worker 请求，直接误导 worker。
        self.inject_task_tail: bool = True

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
        尾段：游离态挂尾——阅后即焚，不进 self.log，利用近因效应聚焦当前任务步骤 +
        思考牙/证据牙（防复读、防证据不全就下结论）。
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
        cleaned = [self._clean_log_msg(m) for m in kept]
        # 中段视图变换：折叠陈旧大工具结果（不动 self.log，原文照常落盘/可 recall）
        cleaned = self._collapse_stale_tool_results(cleaned)
        api.extend(cleaned)

        # ── 游离态挂尾注入（阅后即焚，不进 self.log）──
        try:
            from .tasks import read_current_active_step
            _active = read_current_active_step() if self.inject_task_tail else ""
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

        # ── 行为牙：思考牙 + 证据牙（阅后即焚，不进 self.log）──
        api.append({"role": "system", "content": _THINKING_NUDGE})
        api.append({"role": "system", "content": _EVIDENCE_NUDGE})

        # ── 协议不变式终强制（唯一一层）：tool 结果必须**紧邻**其 assistant 之后 ──
        # DeepSeek 的校验是紧邻性不是存在性：assistant(tool_calls) 与 tool 结果之间
        # 插任何消息（实测 system 也算）都 400"insufficient tool messages"。此前的
        # 存在性补占位/位置校验都拦不住这类，已合并为这一层邻接强制。
        return self._enforce_tool_pairing(api)

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
    def _nth_user_from_end(msgs: list[dict], n: int) -> int:
        """从尾部数第 n 个 user 消息的位置；不足 n 个（短会话）→ 0（全部算新鲜）。"""
        seen = 0
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                seen += 1
                if seen >= n:
                    return i
        return 0

    @staticmethod
    def _collapse_stale_tool_results(msgs: list[dict]) -> list[dict]:
        """中段视图变换：把陈旧大工具结果折成一行 stub。压力自适应——离上限越近折得越狠。

        log→document（缓存命中不再约束、文字稿洁净优先）：只折 role==tool 的大结果、
        只动发出去的副本（msgs 已是 _clean_log_msg 产物），self.log 原文不动 → 照常落盘 +
        fetch_tool_result 可取回（"驱逐到磁盘不销毁"）。配对完整、一个开关全关、完全可逆。

        三档舒适区（pre-fold 体积，//4 估，自含、不调 _live_context_tokens 防递归）：
          < comfort_zone_low      宽松：不折，全保真（在舒适区下方、有空间，零 fetch 摩擦）；
          [low, high)             正常：keep_turns / threshold（routinely 折过气信息，稳住）；
          ≥ comfort_zone_high     紧逼：squeeze_keep_turns / squeeze_threshold（更狠），把 live
                                   拉回舒适区。spike 那轮大读在热区不折、下一轮过气即折 → 1-2 轮归位。
        stub 始终是省最多的那刀（不退让到 head+tail——那会留更多、省更少，反令 live 更高）。
        """
        from .params import CONTEXT
        if not CONTEXT.stale_tool_collapse_enabled:
            return msgs

        # 折叠量按 pre-fold 体积估（与 stats 同口径 //4）；自含、绝不调 _live_context_tokens
        # （后者会调 send()→本函数，递归）。绝对 token 对齐「15-25w」心智、与 MAX 解耦。
        approx_tokens = sum(len(m.get("content") or "") for m in msgs) // 4
        if approx_tokens < CONTEXT.comfort_zone_low:
            return msgs
        if approx_tokens >= CONTEXT.comfort_zone_high:
            keep_turns = CONTEXT.stale_tool_squeeze_keep_turns
            threshold = CONTEXT.stale_tool_squeeze_threshold
        else:
            keep_turns = CONTEXT.stale_tool_keep_turns
            threshold = CONTEXT.stale_tool_collapse_threshold

        fresh_start = CacheContext._nth_user_from_end(msgs, keep_turns)
        out: list[dict] = []
        for idx, m in enumerate(msgs):
            content = m.get("content")
            if (idx < fresh_start and m.get("role") == "tool"
                    and isinstance(content, str) and len(content) > threshold):
                out.append({**m, "content": _stub_tool_content(content, m.get("tool_call_id", ""))})
            else:
                out.append(m)
        return out

    @staticmethod
    def _enforce_tool_pairing(msgs: list[dict]) -> list[dict]:
        """焊死 OpenAI/DeepSeek 协议不变量：tool 结果必须**紧邻**其 assistant 之后。

        协议校验是紧邻性不是存在性（实测 2026-07-03）：assistant(tool_calls) 与
        tool 结果之间插任何消息（system 也算）→ 400 "insufficient tool messages
        following tool_calls message"。会破坏紧邻性的真实来源：
          - 工具循环内往 log append 非 tool 消息（如 load_psyche 注入，根因已修
            ——挪到结果写完后；此处是防同类回归的咽喉兜底）；
          - 中断/异常/崩溃留下光杆 tool_calls（缺结果）；
          - 压缩/摘要/加载旧会话导致的乱序（结果跑到 assistant 之前）。

        算法：每个 assistant(tool_calls) 之后按声明顺序紧邻放置其 tool 结果
        （从原位前移/后移），缺失的补占位；被搬走的结果在原位跳过。健康 log
        输出与输入完全一致（同对象同顺序，零缓存影响）。孤儿 tool 消息
        （没有任何 assistant 声明它）不动——另一类问题，不在此臆测处理。
        """
        claimed: set[str] = {
            tc.get("id")
            for m in msgs if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or []) if tc.get("id")
        }
        if not claimed:
            return msgs
        # tool_call_id → 首个结果消息（重复 id 的后续出现留在原位，不参与搬运）
        result_by_id: dict[str, dict] = {}
        for m in msgs:
            if m.get("role") == "tool":
                tcid = m.get("tool_call_id")
                if tcid and tcid not in result_by_id:
                    result_by_id[tcid] = m

        emitted: set[str] = set()
        out: list[dict] = []
        for m in msgs:
            if m.get("role") == "tool":
                tcid = m.get("tool_call_id")
                if tcid in claimed and result_by_id.get(tcid) is m:
                    continue  # 在其 assistant 之后的紧邻位发出（见下）
            out.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tcid = tc.get("id")
                    if not tcid or tcid in emitted:
                        continue
                    emitted.add(tcid)
                    result = result_by_id.get(tcid)
                    out.append(result if result is not None else {
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
