"""LLM 后端抽象：多模型支持、自动路由、流式调用。"""

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from .cache_context import CacheContext
from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    MAX_CONTEXT_TOKENS,
    MAX_RETRIES,
    MODEL_PRO,
    PRO_MAX_TOKENS,
    RETRY_BASE_DELAY,
    TOOL_LOOP_THRESHOLD,
)
from .params import CONTEXT, RUNTIME
from .tools import execute_tool, get_tools

# 工具显示标签（安全确认 + 终端回显共用）
from .tools._tool_meta import PARALLEL_SAFE as _PARALLEL_SAFE
from .tools._tool_meta import SKIP_COMPRESS_TOOLS as _SKIP_COMPRESS_TOOLS
from .tools.tokens import count_messages_tokens, truncate_to_budget

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str], None]
ToolCallback = Callable[[str, dict], None]

RETRYABLE = (
    APITimeoutError, RateLimitError, APIConnectionError, InternalServerError,
    OSError,
)


# ═══════════════════════════════════════════════════════════════
# 用户中断机制：监听 Esc → 设置标志 → 流式检查 → 中断
# ═══════════════════════════════════════════════════════════════


class UserInterruptError(Exception):
    """用户主动中断（按 Esc 触发）。"""

    pass


_interrupt_event = threading.Event()


def request_interrupt() -> None:
    """请求中断当前流式回复。"""
    _interrupt_event.set()


def clear_interrupt() -> None:
    """清除中断标志（每次对话前调用）。"""
    _interrupt_event.clear()


def is_interrupt_requested() -> bool:
    """检查是否收到中断请求。"""
    return _interrupt_event.is_set()


# ═══════════════════════════════════════════════════════════════
# 后端抽象
# ═══════════════════════════════════════════════════════════════


@dataclass
class ModelInfo:
    """模型元信息。"""

    id: str                    # 模型 ID，如 deepseek-v4-pro
    name: str                  # 显示名称，如 "Pro"
    provider: str = "deepseek" # 提供商
    supports_tools: bool = True   # 是否支持 function calling
    supports_stream: bool = True  # 是否支持流式
    supports_thinking: bool = False # 是否有思考过程
    max_tokens: int = 8192     # 最大输出 token


def _reasoning_fallback(reasoning: str) -> str:
    """模型本轮只产出思考(reasoning_content)、没产出正式 content 时的兜底文本。

    不处理的话：思考计入 completion_tokens 但 content 为空 → 屏幕全空 + history 存空
    assistant。把思考兜出来显示并留存，至少不丢、不空。正常有 content 的回复不走这里。
    """
    return "[模型本轮只输出了思考、未给正式回复]\n\n" + reasoning


class LLMBackend(ABC):
    """LLM 后端抽象基类。所有模型后端都实现这个接口。"""

    def __init__(self, model_info: ModelInfo):
        self.info = model_info

    @abstractmethod
    def chat_stream(
        self,
        messages: list[dict],
        on_token: TokenCallback,
        tools: list[dict] | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        """流式调用。返回 (content, tool_calls)。"""
        ...

    @abstractmethod
    def chat_non_stream(
        self,
        messages: list[dict],
        on_token: TokenCallback,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        """非流式调用。返回 (content, tool_calls)。max_tokens 默认用模型上限。"""
        ...


# ═══════════════════════════════════════════════════════════════
# DeepSeek 后端（兼容 OpenAI 协议）
# ═══════════════════════════════════════════════════════════════


class DeepSeekBackend(LLMBackend):
    """DeepSeek 模型后端。API 兼容 OpenAI 协议。"""

    def __init__(self, model_info: ModelInfo):
        super().__init__(model_info)
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
            )
        return self._client

    def chat_stream(
        self,
        messages: list[dict],
        on_token: TokenCallback,
        tools: list[dict] | None = None,
        on_tool_ready: Callable[[int, str, str], None] | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        """流式调用。on_tool_ready(idx, name, args_json) 在工具参数完整时回调。"""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []  # 模型的思考通道(reasoning_content)，正常不显示
        tool_calls: list[dict] = []
        _tc_parsed: set[int] = set()  # 已触发过 on_tool_ready 的索引

        kwargs = {
            "model": self.info.id,
            "messages": messages,
            "stream": True,
            "max_tokens": self.info.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if tools and self.info.supports_tools:
            kwargs["tools"] = tools
        extra_body = _build_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        _set_canonical_request(kwargs)  # 取证：快照真实 wire 请求
        stream = self.client.chat.completions.create(**kwargs)

        try:
            for chunk in stream:
                # 每次收到 chunk 都检查是否被 Esc 中断
                if is_interrupt_requested():
                    stream.close()
                    clear_interrupt()
                    raise UserInterruptError("用户按 Esc 中断")

                # 每个 chunk 都检查 usage（末 chunk 带真实缓存统计，其他 chunk 为 None）
                if hasattr(chunk, "usage") and chunk.usage:
                    _set_api_usage(self.info.name.lower(), chunk.usage)
                _set_system_fingerprint(getattr(chunk, "system_fingerprint", None))

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    on_token(delta.content)
                    content_parts.append(delta.content)
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_parts.append(rc)

                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        while len(tool_calls) <= idx:
                            tool_calls.append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        tc = tool_calls[idx]
                        if tc_delta.id:
                            tc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tc["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tc["function"]["arguments"] += tc_delta.function.arguments
                                if on_tool_ready and idx not in _tc_parsed:
                                    try:
                                        json.loads(tc["function"]["arguments"])
                                        _tc_parsed.add(idx)
                                        on_tool_ready(idx, tc["function"]["name"], tc["function"]["arguments"])
                                    except json.JSONDecodeError:
                                        pass  # 参数还没收完，等下一个 chunk
        except UserInterruptError:
            # 中断时返回已收到的部分内容，但不作为完整回复
            content = "".join(content_parts) if content_parts else None
            if content:
                on_token("\n\n[⚠️ 已中断 — 以上是已收到的部分回复]\n")
            raise  # 重新抛出，让 run_conversation 处理消息清理

        # 流式正常结束
        content = "".join(content_parts) if content_parts else None
        final_tool_calls = tool_calls if tool_calls else None
        if not content and not final_tool_calls and reasoning_parts:
            content = _reasoning_fallback("".join(reasoning_parts))
            on_token(content)  # 否则整屏空白（计入 completion_tokens 却无 content）
        return content, final_tool_calls

    def chat_non_stream(
        self,
        messages: list[dict],
        on_token: TokenCallback,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        kwargs = {
            "model": self.info.id,
            "messages": messages,
            "max_tokens": max_tokens or self.info.max_tokens,
        }
        if tools and self.info.supports_tools:
            kwargs["tools"] = tools
        extra_body = _build_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        _set_canonical_request(kwargs)  # 取证：快照真实 wire 请求
        response = self.client.chat.completions.create(**kwargs)

        # 捕获 API 返回的真实 usage（含 cache hit/miss）
        if hasattr(response, "usage") and response.usage:
            _set_api_usage(self.info.name.lower(), response.usage)
        _set_system_fingerprint(getattr(response, "system_fingerprint", None))

        msg = response.choices[0].message

        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        content = msg.content
        if not content and not tool_calls:
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                content = _reasoning_fallback(reasoning)
        if content:
            on_token(content)

        return content, tool_calls


# ═══════════════════════════════════════════════════════════════
# 模型注册表
# ═══════════════════════════════════════════════════════════════

# 所有可用模型（始终 Pro — 单模型养肥前缀缓存）
AVAILABLE_MODELS: dict[str, LLMBackend] = {
    "pro": DeepSeekBackend(ModelInfo(
        id=MODEL_PRO,
        name="Pro",
        provider="deepseek",
        supports_tools=True,   # deepseek-v4-pro 也支持 function calling
        supports_stream=True,
        supports_thinking=False,
        max_tokens=PRO_MAX_TOKENS,
    )),
}

# 当前模型 — 始终 Pro（DeepSeek KV 缓存按模型独立，固定单模型才能养肥长前缀）
_current_backend: LLMBackend = AVAILABLE_MODELS["pro"]


def get_current_model_name() -> str:
    """返回当前模型的显示名称。"""
    return _current_backend.info.name


def switch_model(name: str) -> tuple[bool, str]:
    """切换当前模型。name 可以是 'pro' 或完整模型 ID。"""
    global _current_backend

    # 先按短名称查找
    if name in AVAILABLE_MODELS:
        _current_backend = AVAILABLE_MODELS[name]
        info = _current_backend.info
        return True, f"已切换到 {info.name}（{info.id}）"

    # 按完整模型 ID 查找
    for _key, backend in AVAILABLE_MODELS.items():
        if backend.info.id == name:
            _current_backend = backend
            info = backend.info
            return True, f"已切换到 {info.name}（{info.id}）"

    available = ", ".join(f"{k}={v.info.id}" for k, v in AVAILABLE_MODELS.items())
    return False, f"未知模型: {name}。可用: {available}"


def list_models() -> str:
    """列出所有可用模型。"""
    lines = ["可用模型：\n"]
    for key, backend in AVAILABLE_MODELS.items():
        info = backend.info
        current = "← 当前" if backend is _current_backend else ""
        lines.append(
            f"  {key:<8} {info.id:<24} {current}"
        )
        lines.append(f"          工具调用: {'✅' if info.supports_tools else '❌'}  "
                      f"流式: {'✅' if info.supports_stream else '❌'}  "
                      f"最大输出: {info.max_tokens}")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 模型选择
# ═══════════════════════════════════════════════════════════════

def auto_select_model(user_input: str) -> LLMBackend:
    """始终使用 Pro。"""
    return AVAILABLE_MODELS["pro"]



# 统计持久化目录：agent/stats/{session_id}.json
_STATS_DIR = Path(__file__).resolve().parent.parent / "stats"

# 取证开关：CTG_DUMP_PAYLOADS=1 时把每次真实发给 API 的 canonical_request（完整请求字段：
# model/messages/tools/max_tokens/stream/stream_options/extra_body）+ usage + system_fingerprint
# + messages_hash/tools_hash/request_hash 一起落盘，供 scripts/payload_diff.py 相邻对账：
# 一锤定音「命中塌陷是客户端 payload 变了，还是 DeepSeek 服务端缓存未命中」。默认关：零落盘零开销。
_DUMP_PAYLOADS = os.getenv("CTG_DUMP_PAYLOADS", "").strip().lower() not in ("", "0", "false", "no")
_PAYLOAD_DIR = _STATS_DIR / "payloads"

# 每次请求带恒定 user_id（默认开）：extra_body={"user_id": "ctgents"}，对所有请求/会话
# 恒定不变。若 DeepSeek 按 user_id 做缓存亲和/分区，恒定 key 才能让前缀缓存跨请求稳定复用；
# 旧版按 <session> 取 key——session_id 一变就换 key、缓存断片（用户要求"每次 user_id 必须相同"）。
# CTG_KVCACHE_USER_ID=0 可关（万一 DeepSeek 拒收 extra_body 报 400 时降级用）。
_KVCACHE_USER_ID = os.getenv("CTG_KVCACHE_USER_ID", "1").strip().lower() not in ("0", "false", "no")

# 取证用运行时快照（与 _last_api_usage 同模式：请求时写、落盘时读）。
_last_canonical_request: dict | None = None      # 上次真实 wire 请求字段
_last_system_fingerprint: str | None = None      # 上次响应的后端节点指纹（DeepSeek 可能不返回）
_prev_tools_hash: str | None = None              # 上次 tools schema 哈希（识别 tools 变化）

# 空统计模板
_EMPTY_STATS = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "cache_hit_tokens": 0, "cache_miss_tokens": 0}

# 每请求明细：存于 stats["history"]，保留最近 N 条（揪 miss 突刺用）。
# total 聚合只算 _EMPTY_STATS 的数值键，history 是 list、不参与求和。
_HISTORY_LIMIT = 50

# 单轮增量累加器：每个真实请求就地累加，note_turn_start 复位、note_turn_end 读取。
# 不走"快照全局累计再做差"——那条路会被会话切换时 _ensure_session 换/重置
# _CACHE_STATS 指针击穿（""→真 id 那次本轮写入对两次快照不可见 → 增量全 0）。
_turn_accum = {"requests": 0, "completion_tokens": 0, "miss": 0}


def reset_turn_accum() -> None:
    """一轮开始前复位单轮累加器。"""
    _turn_accum.update(requests=0, completion_tokens=0, miss=0)


def get_turn_accum() -> dict:
    """读单轮累加器快照（请求数 / 输出 token / miss）。"""
    return dict(_turn_accum)


def _trailing_system_tokens(messages: list[dict]) -> int:
    """量本次 payload 尾部注入的 token：send() 把 system 消息排到末尾，
    末尾连续的 system 段就是尾部（工具循环 skip_volatile 时它更短甚至为空）。
    前缀的 system 在开头、不计。
    """
    from .tools.tokens import estimate_tokens
    total = 0
    for m in reversed(messages):
        if m.get("role") != "system":
            break
        total += estimate_tokens(m.get("content") or "")
    return total


_last_req_time = None      # 上一条请求的时刻（monotonic），算请求间隔用
_prev_payload_sig = None   # 上次 payload 的逐消息 (hash, est_tok)，算最长公共前缀


def _payload_fingerprint(messages: list[dict]) -> dict:
    """量本次 payload 的结构指纹，供突刺取证（命中塌了到底是谁的锅）。

    中段 = send() 里排在 prefix 之后、tail 之前的非 system 消息，正常只追加不改：
      n    = 中段消息条数（缩了=有东西删/改了旧消息）。
      fe   = 中段「前沿」哈希（最靠前 3 条内容）。本该恒定（最早几轮对话从不变）：
             突刺时 fe 变 = 靠前旧消息被原地改写（我们的 bug，往代码里查）。
      g    = 距上条请求秒数。大间隔→疑 TTL；毫秒级→排除 TTL（服务端容量/LRU）。
      lcpr = 与上一条 payload 的最长公共前缀占本次的比例（按 token 估算）。
             这就是「本该命中」的上限——前缀缓存对未变的前缀必命中。
             实命中率 << lcpr = 我们发过、之前缓存过的前缀被服务端吃了（不是新内容 miss，
             答得了"纯追加为何命中反降"）；实命中率 ≈ lcpr = miss 全在新追加后缀，预期内。
      th_chg = tools schema 相比上一条是否变了。lcpr 只看 messages，会漏掉「工具表变了
             导致前缀整体作废」这类——tools 在 DeepSeek 前缀里排在 messages 前，它一变、
             后面 messages 再相同也全 miss。正常恒为 False；一旦 True，突刺锅在 tools 不在 messages。
    用比例而非绝对值：抵消 estimate_tokens 与真实 token 的系统性尺度差（两边同尺度相除）。
    """
    import time

    from .tools.tokens import estimate_tokens
    global _last_req_time, _prev_payload_sig, _prev_tools_hash
    nonsys = [m for m in messages if m.get("role") != "system"]
    front = "\x00".join((m.get("content") or "")[:500] for m in nonsys[:3])
    fe = hashlib.sha256(front.encode("utf-8", "ignore")).hexdigest()[:8] if nonsys else "-"

    # 逐消息签名（顺序即 payload 顺序），含 tool_calls 以防工具调用变化被漏掉
    sig: list[tuple[str, int]] = []
    for m in messages:
        key = m.get("role", "") + "\x00" + (m.get("content") or "")
        if m.get("tool_calls"):
            key += "\x00tc" + str(len(m["tool_calls"]))
        h = hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:8]
        sig.append((h, estimate_tokens(m.get("content") or "")))
    total_est = sum(t for _h, t in sig) or 1
    lcp = 0
    if _prev_payload_sig:
        for (h1, t1), (h2, _t2) in zip(sig, _prev_payload_sig, strict=False):
            if h1 != h2:
                break
            lcp += t1
    _prev_payload_sig = sig
    lcpr = round(lcp / total_est, 3)

    # tools/request 变化识别（lcpr 只看 messages，这里补上 tools 表变化）
    th = _hash_obj(_last_canonical_request.get("tools")) if _last_canonical_request else None
    th_chg = bool(_prev_tools_hash and th and th != _prev_tools_hash)
    if th:
        _prev_tools_hash = th

    now = time.monotonic()
    gap = 0.0 if _last_req_time is None else round(now - _last_req_time, 1)
    _last_req_time = now
    return {"n": len(nonsys), "fe": fe, "g": gap, "lcpr": lcpr, "th_chg": th_chg}

# 当前会话 ID 和内存中的统计（切换会话时自动读写文件）
_current_session_id: str = ""
_CACHE_STATS: dict[str, dict] = {
    "pro": dict(_EMPTY_STATS),
}


# API 返回的真实 usage（运行时数据，不持久化）
_last_api_usage: dict[str, dict | None] = {"pro": None}


def _stats_path(session_id: str) -> Path:
    """返回指定会话的统计文件路径。"""
    return _STATS_DIR / f"{session_id}.json"


def _load_cache_stats(session_id: str) -> dict[str, dict]:
    """从文件加载指定会话的统计，文件不存在或损坏则返回空统计。"""
    try:
        p = _stats_path(session_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("stats file corrupted (not a dict), resetting")
                return {"pro": dict(_EMPTY_STATS)}
            if "pro" not in data:
                data["pro"] = dict(_EMPTY_STATS)
            return data
    except Exception:
        pass
    return {"pro": dict(_EMPTY_STATS)}


def _save_cache_stats(session_id: str) -> None:
    """将当前内存统计写入指定会话的文件。"""
    try:
        _STATS_DIR.mkdir(parents=True, exist_ok=True)
        p = _stats_path(session_id)
        p.write_text(json.dumps(_CACHE_STATS, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    except Exception:
        pass  # 写入失败不阻塞


def _dump_payload(session_id: str, req_idx: int, messages: list[dict], usage: dict | None) -> None:
    """把本次真实发给 API 的 canonical_request 完整落盘（CTG_DUMP_PAYLOADS 开时）。

    存 stats/payloads/<session>/req_<NNNN>.json，含：
      canonical_request  = 真实 wire 请求字段（model/messages/tools/max_tokens/...）；
      usage              = 真实 p/h/miss/c；
      system_fingerprint = 后端节点指纹（变化=请求路由到了别的节点，可能就是缓存未命中主因）；
      messages_hash / tools_hash / request_hash = 供 payload_diff 一眼看出到底哪部分变了。
    这是不带任何估算的地面真相：相邻 canonical_request 字节相同的前缀 = 前缀缓存「本该命中」的硬上限。
    """
    if not _DUMP_PAYLOADS:
        return
    with contextlib.suppress(Exception):
        canonical = _last_canonical_request or {"messages": messages}
        msgs = canonical.get("messages", messages)
        rec = {
            "req": req_idx,
            "usage": usage,
            "system_fingerprint": _last_system_fingerprint,
            "messages_hash": _hash_obj(msgs),
            "tools_hash": _hash_obj(canonical.get("tools")),
            "request_hash": _hash_obj(canonical),
            "canonical_request": canonical,
        }
        d = _PAYLOAD_DIR / (session_id or "_nameless")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"req_{req_idx:04d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_session(session_id: str) -> None:
    """确保内存中是目标会话的统计。如果会话切换，自动保存旧会话、加载新会话。"""
    global _current_session_id, _CACHE_STATS

    if not session_id:
        return
    if session_id == _current_session_id:
        return

    # 无名会话("")→新分配的真 id：把已累计的统计搬过去，别丢首请求。
    # 新会话首请求落在 session_id="" 上，随后 on_progress 保存才分配真 id——
    # 若此时直接 load 真 id（空文件）会把那个首请求统计丢掉（per-session 累计 -1，
    # 单请求会话直接变 0 → /context 整段消失）。仅当真 id 尚无统计文件时搬运。
    if not _current_session_id and not _stats_path(session_id).exists():
        _current_session_id = session_id
        _save_cache_stats(session_id)
        return

    # 保存当前会话的统计
    if _current_session_id:
        _save_cache_stats(_current_session_id)

    # 切换到新会话
    _current_session_id = session_id
    _CACHE_STATS = _load_cache_stats(session_id)


def _set_api_usage(model_key: str, usage: object | None) -> None:
    """记录本次 API 返回的 usage 数据（非流式响应或流式末 chunk）。"""
    global _last_api_usage
    if usage is None:
        return
    with contextlib.suppress(Exception):
        _last_api_usage[model_key] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
            "cache_miss_tokens": getattr(usage, "prompt_cache_miss_tokens", 0) or 0,
        }


def _hash_obj(obj: object) -> str:
    """对任意可 JSON 化对象做稳定短哈希（键排序，供 messages/tools/request 变化识别）。"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8", "ignore")
    ).hexdigest()[:16]


def _build_extra_body() -> dict | None:
    """每次请求带恒定 user_id（默认开）：跨所有请求/会话恒为 "ctgents"，绝不随 session
    变——DeepSeek 若按 user_id 做缓存亲和，恒定 key 才能让前缀缓存稳定复用。
    """
    if _KVCACHE_USER_ID:
        return {"user_id": "ctgents"}
    return None


def _set_canonical_request(kwargs: dict) -> None:
    """落盘取证：快照本次真实发出的 wire 请求字段（kwargs 即 canonical_request）。"""
    global _last_canonical_request
    _last_canonical_request = {
        k: kwargs.get(k) for k in
        ("model", "messages", "tools", "max_tokens", "stream", "stream_options", "extra_body")
        if k in kwargs
    }


def _set_system_fingerprint(fp: object) -> None:
    """记录响应的 system_fingerprint（后端节点指纹；DeepSeek 可能不返回则保持 None）。"""
    global _last_system_fingerprint
    if fp:
        _last_system_fingerprint = str(fp)


def _update_cache_stats(model_key: str, messages: list[dict], session_id: str = "") -> None:
    """每次 API 请求后更新当前会话的缓存统计，并持久化。

    优先使用 API 返回的真实 cache_hit/cache_miss 数据，
    session_id 用于隔离不同会话的统计数据。
    """
    global _CACHE_STATS

    _ensure_session(session_id)

    stats = _CACHE_STATS.setdefault(model_key, dict(_EMPTY_STATS))
    stats["requests"] += 1
    _turn_accum["requests"] += 1

    # 优先用 API 返回的真实数据
    usage = _last_api_usage.get(model_key)
    if usage and usage.get("prompt_tokens", 0) > 0:
        stats["prompt_tokens"] += usage["prompt_tokens"]
        stats["completion_tokens"] += usage["completion_tokens"]
        stats["cache_hit_tokens"] += usage["cache_hit_tokens"]
        stats["cache_miss_tokens"] += usage["cache_miss_tokens"]
        _turn_accum["completion_tokens"] += usage["completion_tokens"]
        _turn_accum["miss"] += usage["cache_miss_tokens"]
        # 记每请求明细：p=输入 h=命中（miss=p-h 派生）t=本次尾部注入，
        # 外加结构指纹 n/fe/g（突刺取证用，见 _payload_fingerprint）。保留最近 N 条。
        hist = stats.setdefault("history", [])
        fp = _payload_fingerprint(messages)
        hist.append({"p": usage["prompt_tokens"], "h": usage["cache_hit_tokens"],
                     "c": usage["completion_tokens"],
                     "t": _trailing_system_tokens(messages),
                     "n": fp["n"], "fe": fp["fe"], "g": fp["g"], "lcpr": fp["lcpr"],
                     "th_chg": fp.get("th_chg", False)})
        if len(hist) > _HISTORY_LIMIT:
            del hist[:-_HISTORY_LIMIT]
        _dump_payload(_current_session_id, stats["requests"], messages, usage)
        _last_api_usage[model_key] = None  # 消费掉，防止重复计数
        _save_cache_stats(_current_session_id)
        return

    # 后备：API 未返回 usage 时不估算 hit/miss，只记 prompt_tokens 总量
    from .tools.tokens import count_messages_tokens
    total = count_messages_tokens(messages)
    stats["prompt_tokens"] += total
    _save_cache_stats(_current_session_id)


def get_cache_stats(session_id: str = "") -> dict:
    """返回指定会话的缓存命中统计，供 /context 使用。"""
    data = _load_cache_stats(session_id) if session_id and session_id != _current_session_id else _CACHE_STATS

    if not isinstance(data, dict):
        data = {"pro": dict(_EMPTY_STATS)}

    models: dict[str, dict] = {}
    total = dict(_EMPTY_STATS)
    for key, stats in data.items():
        if key == "pro" and isinstance(stats, dict):
            models[key] = dict(stats)
            for k in total:
                total[k] += stats.get(k, 0)
    return {"models": models, "total": total}
# 对话循环（核心）
# ═══════════════════════════════════════════════════════════════


def _invoke_llm(
    backend: LLMBackend,
    messages: list[dict],
    on_token: TokenCallback,
    session_id: str = "",
    track_stats: bool = True,
    tools: list[dict] | None = None,
) -> tuple[str | None, list[dict] | None]:
    """调用 LLM，带重试机制。首次流式，失败后降级为非流式重试。

    tools=None 时自动取 get_tools()；传入空列表 [] 则无工具调用。
    """
    model_key = backend.info.name.lower()
    if tools is None:
        tools = get_tools() if backend.info.supports_tools else None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                result = backend.chat_stream(messages, on_token, tools)
            else:
                logger.warning("流式失败，降级为非流式重试 (%d/%d)...", attempt, MAX_RETRIES)
                result = backend.chat_non_stream(messages, on_token, tools)
            if track_stats:
                _update_cache_stats(model_key, messages, session_id)
            return result
        except RETRYABLE as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("网络波动 (%s)，重试中 (%d/%d)...", e, attempt, MAX_RETRIES)
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise

    raise RuntimeError("unreachable")


def _collect_eager_results(
    pending: list[tuple[int, "Future[str]", str, dict]],
    pre_results: dict[int, str],
    lock: threading.Lock,
) -> None:
    """收集 eager 预执行工具的轮询结果，含 Tavily 自愈。"""
    for idx, fut, name, args in pending:
        result = fut.result(timeout=30)
        if name == "search_web" and _is_quota_error(result):
            healed = _tavily_self_heal()
            if healed:
                retry_tc = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                result = _tracked_execute_tool(retry_tc)
        with lock:
            pre_results[idx] = result


def _try_llm_call(
    backend: "LLMBackend",
    messages: list[dict],
    on_token: TokenCallback,
    tools: list[dict] | None,
    on_tool_ready: Callable | None,
    attempt: int,
    max_retries: int,
) -> tuple[str | None, list[dict] | None]:
    """执行一次 LLM 调用：首次流式，后续降级非流式。"""
    if attempt == 1:
        return backend.chat_stream(
            messages, on_token, tools, on_tool_ready=on_tool_ready,
        )
    logger.warning(
        "流式失败，降级为非流式重试 (%d/%d)...", attempt, max_retries,
    )
    return backend.chat_non_stream(messages, on_token, tools)


def _invoke_llm_eager(
    backend: LLMBackend,
    messages: list[dict],
    on_token: TokenCallback,
    session_id: str = "",
    track_stats: bool = True,
    tools: list[dict] | None = None,
) -> tuple[str | None, list[dict] | None, dict[int, str]]:
    """Eager 工具执行：LLM 流式期间预启动 SAFE 工具。

    与 _invoke_llm 的差异：
    - chat_stream 传入 on_tool_ready 回调
    - 每个 SAFE 工具参数完整后立即提交线程池（与剩余流式并行）
    - 流结束时 join 所有 future，返回 pre_results
    - 非 SAFE 工具和 JSON 解析失败的工具 → 留在 tool_calls 中正常返回

    Returns:
        (content, tool_calls, pre_results)
        pre_results: {tool_call_index_in_response: result_string}
    """
    model_key = backend.info.name.lower()
    if tools is None:
        tools = get_tools() if backend.info.supports_tools else None

    pre_results: dict[int, str] = {}
    pending: list[tuple[int, Future[str], str, dict]] = []
    lock = threading.Lock()
    executor = _get_eager_executor()

    def on_tool_ready(idx: int, name: str, args_json: str) -> None:
        """流式线程回调：参数完整 → 若是 SAFE 工具，立即提交执行。"""
        # poll 是阻塞的时变读：预跑它占线程 + 刷屏、且省不了延迟（后台作业已改为完成
        # 自动通知、不靠 poll）。其余 parallel-safe 读照常 eager。
        if name not in _PARALLEL_SAFE or name == "poll":
            return
        try:
            args = json.loads(args_json)
        except json.JSONDecodeError:
            return
        tc = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args_json))
        fut = executor.submit(_tracked_execute_tool, tc)
        with lock:
            pending.append((idx, fut, name, args))
        print(f"  ⚡ [Eager] {name}({args_json[:60]}...) 已提交（流未结束）")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            content, tool_calls = _try_llm_call(
                backend, messages, on_token, tools, on_tool_ready,
                attempt, MAX_RETRIES,
            )

            # ── 收集 eager 结果 ──
            _collect_eager_results(pending, pre_results, lock)

            if track_stats:
                _update_cache_stats(model_key, messages, session_id)
            return content, tool_calls, pre_results

        except RETRYABLE as e:
            pre_results.clear()
            pending.clear()
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning("网络波动 (%s)，重试中 (%d/%d)...", e, attempt, MAX_RETRIES)
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise

    raise RuntimeError("unreachable")


# ═══════════════════════════════════════════════════════════════
# API 消息构建（前缀缓存友好版）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 工具结果压缩（Phase 2：缓存优化）
# ═══════════════════════════════════════════════════════════════

# 压缩阈值（字符数，约等于 token 数）——真值在 params.RUNTIME
_TOOL_RESULT_COMPRESS_THRESHOLD = RUNTIME.tool_result_compress_threshold
# 省略标记自身约 60-100 字符，省略量低于此值时压缩得不偿失
_COMPRESS_MIN_OMITTED = 120
# 单轮请求数熔断——真值在 params.RUNTIME
_MAX_REQUESTS_PER_TURN = RUNTIME.max_requests_per_turn
# 工具循环请求是否携带尾部 volatile。默认 keep（=1）。
# 待验证假设：曾默认 skip 省 token，疑似导致 [T]→第一个[L] 命中塌回前缀（~37%，本该~96%）；
# 改 keep 让轮内 payload shape 不切换，看能否消除塌陷。**尚未实测**——需开 CTGents 跑一轮
# 带工具的对话、看 /context 第一个[L]是否仍塌；机制（为何 skip 丢命中）也未坐实，勿当结论。
# 设 CTG_TOOLLOOP_KEEP_TAIL=0 恢复旧行为（skip）。
_KEEP_TOOLLOOP_TAIL = os.getenv("CTG_TOOLLOOP_KEEP_TAIL", "1").strip().lower() not in ("", "0", "false", "no")
# 诊断实验：CTG_NO_VOLATILE_TAIL=1 时每个请求都跳过全部尾部 volatile（stance/任务/钉板都不发），
# payload 变成纯 [前缀][对话]、可证明只追加。用于终结"命中塌陷是我们的代码还是服务端"之争：
# 开着仍塌=代码已证明只追加→服务端淘汰；开着不塌=尾部那套在作怪。默认关。
_NO_VOLATILE_TAIL = os.getenv("CTG_NO_VOLATILE_TAIL", "").strip().lower() not in ("", "0", "false", "no")
# 干了不少活却没建任务 → 提示建任务的请求数阈值——真值在 params.RUNTIME
_TASK_SUGGEST_MIN_REQUESTS = RUNTIME.task_suggest_min_requests
# eager 工具执行线程池（LLM 流式期间预启动 SAFE 工具，持久复用）
_EAGER_EXECUTOR: _ThreadPoolExecutor | None = None


def _is_quota_error(result: str) -> bool:
    """检测工具结果是否为 Tavily 配额耗尽错误——统一检测，替代逐层 try/except。"""
    return (
        "UsageLimitExceededError" in result
        or "plan's set usage limit" in result
    )


def _tavily_self_heal() -> bool:
    """重读 .env → 重建 tavily 客户端 → 更新 src.tools.web 模块级变量。

    在 eager executor 层（而非在 search_web 内）接线——当下层模块因旧版本
    内存态未加载 _try_self_heal 时，这层兜底。返回 True 表示自愈成功。
    """
    try:
        import os
        from pathlib import Path

        from dotenv import load_dotenv

        import src.tools.web as _web

        from .config import MultiKeyTavilyClient

        env_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(env_path, override=True)
        keys_raw = os.getenv("TAVILY_API_KEYS", os.getenv("TAVILY_API_KEY", ""))
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        if not keys:
            return False
        _web.tavily = MultiKeyTavilyClient(keys)
        logger.info("Tavily 自愈：已更新 web.tavily 客户端（%d keys）", len(keys))
        return True
    except Exception as exc:
        logger.warning("Tavily 自愈失败: %s", exc)
        return False


def _get_eager_executor() -> _ThreadPoolExecutor:
    global _EAGER_EXECUTOR
    if _EAGER_EXECUTOR is None:
        _EAGER_EXECUTOR = _ThreadPoolExecutor(
            max_workers=RUNTIME.eager_executor_workers,
            thread_name_prefix="eager-tool",
        )
    return _EAGER_EXECUTOR


# 大结果里"内容即噪声"的工具：超阈值的大输出 = 把已知输入回显（write_file 回显
# 刚写入的文件、edit/delete 回执），换成 stub 不丢信号。其余工具的大输出含信号
# （测试失败行/grep 命中/汇总/退出码），用头尾保留而非整段丢弃。
_STUB_WHEN_LARGE = frozenset({"write_file", "edit_file_lines", "delete_file"})

# 头尾保留预算（字符）：尾部多留——pytest 汇总/退出码/最终报错都在尾部。
_KEEP_HEAD_CHARS = 1000
_KEEP_TAIL_CHARS = 1200


def _summarize_tool_result(tool_name: str, result: str) -> str:
    """压缩过大工具结果，但保留信号（不再把内容整个换成 stub）。

    旧版对非 read 工具一律换成 `[tool] (N 字符)`，把测试失败/grep 命中全扔了——
    agent 看不到失败就重跑 → 更多请求更多 token，反成缓存负担。改为头尾保留：
    失败行多在前、汇总/退出码在尾，两端都不丢，中段才省。read_file 仍由
    SKIP_COMPRESS 豁免（在 _compress_tool_result 提前返回，不进这里）。
    """
    if tool_name in _STUB_WHEN_LARGE:
        return f"[{tool_name}] 完成（输出 {len(result):,} 字符已省略，内容即已写入的输入）"
    return _head_tail_keep(result, tool_name)


def _head_tail_keep(text: str, tool_name: str) -> str:
    """保留头部 + 尾部、省中段，并尽量贴行边界。比首尾截断更稳：尾部信号不丢。"""
    total = len(text)
    if total <= _KEEP_HEAD_CHARS + _KEEP_TAIL_CHARS:
        return text
    head = text[:_KEEP_HEAD_CHARS]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    tail = text[-_KEEP_TAIL_CHARS:]
    nl = tail.find("\n")
    if nl >= 0:
        tail = tail[nl + 1:]
    omitted = total - len(head) - len(tail)
    return (
        f"{head}\n\n"
        f"…（[{tool_name}] 共 {total:,} 字符，省略中段 {omitted:,} 字符；"
        f"失败行/汇总/退出码通常在尾部，已保留）…\n\n"
        f"{tail}"
    )


def _compress_tool_result(tool_name: str, result: str) -> str:
    """压缩过大的工具结果：小于阈值不动，否则语义摘要。

    read_file/read_file_lines → 不压缩（token 动态截断已够用）。
    其余工具 → 语义摘要，比首尾截断更紧凑、更可续做。
    """
    if tool_name in _SKIP_COMPRESS_TOOLS or len(result) <= _TOOL_RESULT_COMPRESS_THRESHOLD:
        return result
    return _summarize_tool_result(tool_name, result)

# ═══════════════════════════════════════════════════════════════
# 对话上下文优化（Append-Only — 对齐 Reasonix 缓存优先策略）
# ═══════════════════════════════════════════════════════════════

# 上下文/压缩旋钮统一定义在 params.CONTEXT；此处绑定本地名，保持模块内引用与可 monkeypatch。
_COMPACT_THRESHOLD = CONTEXT.compact_threshold          # 滑窗压缩触发比例 (65%)
_COMPACT_KEEP_RATIO = CONTEXT.compact_keep_ratio        # 压缩后保留最近 N% 消息
# ── 压缩防抖状态 ──
_previous_summary: str | None = None          # 上次压缩的摘要，供迭代更新
_ineffective_compression_count: int = 0       # 连续低效压缩计数




def _compact_context(ctx, user_input: str, force: bool = False):
    """In-place compaction. Returns None for CacheContext, list for legacy flat list.

    Accepts CacheContext (preferred) or legacy flat list[dict].
    force=True 跳过 65% 门槛，无条件压缩（供手动 /compact 命令使用）。
    """
    from .cache_context import CacheContext

    if isinstance(ctx, CacheContext):
        _compact_cache_context(ctx, user_input, force=force)
    else:
        prefix = [m for m in ctx if m.get("role") == "system"]
        log = [m for m in ctx if m.get("role") != "system"]
        tmp = CacheContext(prefix_msgs=prefix, log_msgs=log)
        _compact_cache_context(tmp, user_input, force=force)
        return tmp.all


def _compact_cache_context(ctx, user_input: str, force: bool = False) -> None:
    """滑窗压缩：超阈值时驱旧消息，替换为摘要。"""
    from .tools.tokens import count_messages_tokens
    global _previous_summary, _ineffective_compression_count

    log = ctx.log
    if not log:
        return

    all_msgs = ctx.prefix + log
    used = count_messages_tokens(all_msgs)

    if not force and not _should_compact(used):
        return

    keep_start = _find_compaction_boundary(log)
    if keep_start < 2 or keep_start >= len(log):
        return

    evicted = log[:keep_start]
    kept = log[keep_start:]
    summary = _make_brief_summary(evicted, previous_summary=_previous_summary)
    if not summary:
        return

    _track_compaction_effectiveness(all_msgs, ctx.prefix, kept)
    _replace_log_with_summary(ctx, kept, evicted, summary)
    _previous_summary = summary
    logger.info("滑窗压缩：驱 %d 条旧消息，保留 %d 条", len(evicted), len(kept))


def _should_compact(used: int) -> bool:
    """检查是否应该触发压缩（用量超阈值且未进入防抖）。"""
    global _ineffective_compression_count
    limit = int(MAX_CONTEXT_TOKENS * _COMPACT_THRESHOLD)
    if used < limit:
        return False
    if _ineffective_compression_count >= 2:
        logger.warning("压缩跳过——最近 %d 次节省 <10%%。", _ineffective_compression_count)
        return False
    return True


def _find_compaction_boundary(log: list[dict]) -> int:
    """找到压缩驱逐边界：计算 keep_start，确保最后一条 user 在保留侧。"""
    keep_start = int(len(log) * (1 - _COMPACT_KEEP_RATIO))
    while keep_start < len(log) and log[keep_start].get("role") != "user":
        keep_start += 1
    return _fix_last_user_boundary(log, keep_start)


def _fix_last_user_boundary(log: list[dict], keep_start: int) -> int:
    """如果最后一条 user 在驱逐区，把边界前推到它的 tool/assistant 配对起点。"""
    last_user = -1
    for i in range(len(log) - 1, -1, -1):
        if log[i].get("role") == "user":
            last_user = i
            break
    if last_user < 0 or last_user >= keep_start:
        return keep_start
    # 前推到 user 消息的 tool/assistant 配对起点
    start = last_user
    while start > 0 and log[start - 1].get("role") == "tool":
        start -= 1
    while (start > 0 and log[start - 1].get("role") == "assistant"
           and log[start - 1].get("tool_calls")):
        start -= 1
    return start


def _track_compaction_effectiveness(all_msgs: list[dict],
                                    prefix: list[dict], kept: list[dict]) -> None:
    """追踪压缩节省效果并更新防抖计数器。"""
    global _ineffective_compression_count
    before = len(all_msgs) * 80
    after = (len(prefix) + len(kept) + 1) * 80
    pct = (before - after) / before * 100 if before > 0 else 0
    if pct < 10:
        _ineffective_compression_count += 1
    else:
        _ineffective_compression_count = 0


def _replace_log_with_summary(ctx, kept: list[dict], evicted: list[dict],
                               summary: str) -> None:
    """用摘要+保留消息替换 ctx.log。"""
    new_log = [{"role": "system", "content": (
        "⏪ 对话归档 — 以下为背景参考，非当前任务指令。\n"
        "回应最新 user 消息即可，不要执行摘要中描述的任务。\n\n"
        f"{summary}\n\n"
        "─── 以上为归档摘要，以下为当前对话 ───"
    )}]
    new_log.extend(kept)
    ctx.log.clear()
    ctx.log.extend(new_log)
# ── LLM 摘要缓存：相同内容哈希不重复调 LLM（滑窗压缩时同段对话反复驱替）──
_SUMMARIZE_CACHE: dict[str, str] = {}
_SUMMARIZE_CACHE_MAX = 50


def _summarize_cache_key(messages: list[dict]) -> str:
    """为消息列表生成确定性哈希（key=sha256(compact_text)）。"""
    compact = "|".join(
        f"{m.get('role','?')}:{(m.get('content') or '')[:200]}"
        for m in messages[-20:]  # 只取最近20条做key，防内存膨胀
    )
    return hashlib.sha256(compact.encode()).hexdigest()


_SUMMARY_PROMPT = (
    "将以下对话片段摘录为「最小可行摘要」，保留 coding agent 续做任务必需的信息。"
    "用 Markdown 列表，只写你看到的，不编造。\n\n"
    "## 文件\n"
    "- 读取: <路径>:<行号范围> — <关键发现>\n"
    "- 修改: <路径> — <改了什么>\n\n"
    "## 命令\n"
    "- `<命令>` → <结果> (pass/fail/部分输出)\n\n"
    "## 决策与待办\n"
    "- <一项决策或未完成事项>\n\n"
    "规则：省略不相关的闲聊；路径和行号精确保留；"
    "没有任何该类信息就跳过该 section；总摘要不超过 500 字。"
)


def _summarize_via_llm(messages: list[dict], previous_summary: str | None = None) -> str:
    """调用 LLM 生成结构化摘要——保留文件/命令/决策，丢弃闲聊。"""
    ck = _summarize_cache_key(messages)
    if ck in _SUMMARIZE_CACHE:
        return _SUMMARIZE_CACHE[ck]

    transcript = _build_summary_transcript(messages)
    if not transcript.strip():
        return ""

    summary = _call_summarizer_llm(transcript)
    if summary:
        _cache_summary(ck, summary)
        return summary

    return _fallback_transcript_extract(messages)


def _build_summary_transcript(messages: list[dict]) -> str:
    """构建摘要用转录：按角色格式化为紧凑文本。"""
    parts = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            parts.append(f"[user] {content[:120]}")
        elif role == "assistant":
            text = content[:200].replace("\n", " ")
            if m.get("tool_calls"):
                names = [tc["function"]["name"] for tc in m["tool_calls"]]
                text += f" [调用: {', '.join(names)}]"
            parts.append(f"[assistant] {text}")
        elif role == "tool":
            label = m.get("_tool_name", "tool")
            parts.append(f"[{label}] {_summarize_tool_result(label, content)}")
    return "\n".join(parts)


def _call_summarizer_llm(transcript: str) -> str | None:
    """调用 LLM 生成摘要，失败返回 None。"""
    payload = [
        {"role": "system", "content": _SUMMARY_PROMPT},
        {"role": "user", "content": f"对话片段：\n{transcript}"},
    ]
    try:
        content, _ = _invoke_llm(
            _current_backend, payload,
            on_token=lambda _t: None, session_id="",
            track_stats=True, tools=[],
        )
        return (content or "").strip() or None
    except Exception as e:
        logger.warning("LLM 摘要失败，回退文本拼接: %s", e)
    return None


def _cache_summary(key: str, summary: str) -> None:
    """将摘要写入缓存，控制上限。"""
    _SUMMARIZE_CACHE[key] = summary
    if len(_SUMMARIZE_CACHE) > _SUMMARIZE_CACHE_MAX:
        _SUMMARIZE_CACHE.pop(next(iter(_SUMMARIZE_CACHE)))


def _fallback_transcript_extract(messages: list[dict]) -> str:
    """摘要 LLM 失败时回退：提取 user 消息首句拼接。"""
    parts = []
    for m in messages:
        c = (m.get("content") or "").strip()
        if m.get("role") == "user" and c:
            parts.append(c[:60].replace("\n", " "))
            if len(parts) >= 5:
                break
    return "、".join(parts) if parts else ""


def _make_brief_summary(messages: list[dict], max_len: int = 500, previous_summary: str | None = None) -> str:
    """压缩摘要入口：优先 LLM 摘要，失败回退文本拼接。"""
    return _summarize_via_llm(messages, previous_summary=previous_summary)


# ═══════════════════════════════════════════════════════════════
# SAFE — 工具并行分发
# SAFE 运行统计（用于 /context 展示）
_safe_stats: dict = {"batches": 0, "parallel_tools": 0, "serial_tools": 0}
_safe_stats_lock = threading.Lock()


def reset_safe_stats() -> None:
    """重置 SAFE 统计。"""
    global _safe_stats
    with _safe_stats_lock:
        _safe_stats = {"batches": 0, "parallel_tools": 0, "serial_tools": 0}


def get_safe_stats() -> dict:
    """返回 SAFE 并行分发统计（线程安全）。"""
    with _safe_stats_lock:
        return dict(_safe_stats)


def _update_safe_stats(n_parallel: int, n_serial: int) -> None:
    """更新 SAFE 统计。"""
    with _safe_stats_lock:
        _safe_stats["batches"] += 1
        _safe_stats["parallel_tools"] += n_parallel
        _safe_stats["serial_tools"] += n_serial



# ── 带计时的工具执行包装（感知层：每调用必记录）──
def _tracked_execute_tool(tc):
    t0 = time.perf_counter()
    try:
        result = execute_tool(tc)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        from .tracker import record_tool_call
        record_tool_call(tc.function.name, elapsed_ms, success=True)
        return result
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        from .tracker import record_tool_call
        record_tool_call(tc.function.name, elapsed_ms, success=False, error=type(e).__name__)
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)

def _execute_tool_batch(approved: list[tuple]) -> list[str]:
    """按序分块并行执行工具 — 保留 LLM 原始调用顺序。

    连续 SAFE 工具 → 并行（同批内顺序无关）
    非 SAFE 工具 → 等待前一批完成后单独执行
    确保 [read(A), run_cmd(改A), read(A)] 中第二个 read 看到改后的 A。
    """
    n = len(approved)

    results: list[str] = [""] * n

    # 先处理有预置结果（跳过）的工具
    for i in range(n):
        pre = approved[i][4]
        if pre is not None:
            results[i] = pre

    total_parallel = 0
    total_serial = 0
    i = 0

    while i < n:
        if approved[i][4] is not None:
            i += 1
            continue

        if approved[i][1] not in _PARALLEL_SAFE:
            results[i] = _tracked_execute_tool(approved[i][3])
            total_serial += 1
            i += 1
        else:
            batch_size = _execute_parallel_batch(approved, i, n, results)
            total_parallel += batch_size
            i += batch_size

    _update_safe_stats(total_parallel, total_serial)
    return results


def _execute_parallel_batch(approved: list[tuple], start: int, n: int,
                            results: list[str]) -> int:
    """收集从 start 开始的连续 SAFE 工具并并行执行。返回执行数。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    batch: list[int] = []
    i = start
    while i < n:
        if approved[i][4] is not None:
            i += 1
            continue
        if approved[i][1] in _PARALLEL_SAFE:
            batch.append(i)
            i += 1
        else:
            break
    if not batch:
        return 0
    names = [approved[j][1] for j in batch]
    print(f"  ⚡ [SAFE] 并行执行 {len(batch)} 个工具: {', '.join(names)}")
    with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as pool:
        fut_map: dict = {}
        for j in batch:
            fut = pool.submit(_tracked_execute_tool, approved[j][3])
            fut_map[fut] = j
        for fut in as_completed(fut_map):
            results[fut_map[fut]] = fut.result()
    return len(batch)


# ═══════════════════════════════════════════════════════════════
# Tool-call JSON 修复 — DeepSeek 常见格式错误
# ═══════════════════════════════════════════════════════════════

def _repair_json(raw: str) -> str:
    """尝试修复 DeepSeek 常见的 JSON 格式错误。

    修复策略（按顺序）：
      1. Python 字面量转换: True/False/None → true/false/null
      2. 单引号 → 双引号（JSON 标准）
      3. 尾部逗号移除（多轮，直到干净）
      4. 缺闭合括号 → 按括号计数补全
      5. 尾部多余字符 → 在最后一个合法括号位置截断
    """
    import re
    s = raw.strip()

    # 1. Python bool/None → JSON
    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    s = re.sub(r'\bNone\b', 'null', s)

    # 2. 单引号键/值 → 双引号（仅在 JSON 上下文安全的情况）
    # 先试原始版本，失败再尝试替换
    if not _try_parse(s):
        s_single = _replace_single_quotes(s)
        if _try_parse(s_single):
            return s_single
        s = s_single  # 去引号后未必单独可解析，但保留它让后续步骤(尾逗号/补括号)接力修

    # 3. 多轮尾部逗号移除
    for _ in range(3):
        new_s = re.sub(r",\s*([}\]])", r"\1", s)
        if new_s == s:
            break
        s = new_s

    # 4. 缺闭合括号：按括号计数补全
    open_braces = s.count("{") - s.count("}")
    open_brackets = s.count("[") - s.count("]")
    if open_braces > 0 or open_brackets > 0:
        s = s + "]" * open_brackets + "}" * open_braces

    # 5. 尾部多余字符：从最后一个有效括号位置截断
    # 使用括号计数找到最后一个平衡点，而不是简单的 rfind("}")
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        # 找到最后一个 } 或 ] 且括号计数平衡的位置
        best_end = _find_valid_truncation_point(s)
        if best_end < len(s):
            s = s[:best_end + 1]
        return s


def _try_parse(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


def _replace_single_quotes(s: str) -> str:
    """将 JSON 键和值的单引号替换为双引号。只替换顶层，不处理嵌套。"""
    import re
    # 键: 'key': → "key":
    s = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', s)
    # 值: : 'value' → : "value"
    s = re.sub(r":\s*'([^']*)'", r': "\1"', s)
    return s


def _find_valid_truncation_point(s: str) -> int:
    """找到最后一个括号计数平衡的 } 或 ] 位置。"""
    brace_depth = 0
    bracket_depth = 0
    last_valid = -1
    for i, ch in enumerate(s):
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0 and bracket_depth == 0:
                last_valid = i
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth -= 1
            if brace_depth == 0 and bracket_depth == 0:
                last_valid = i
    return last_valid

def _check_storm_breaker(
    approved: list[tuple],
    storm_sig: str | None,
    storm_count: int,
) -> tuple[str | None, int]:
    """检测同轮连续同一错误 → 打破死亡螺旋。"""
    cur_sig = _build_failure_signature(approved)
    if cur_sig is None:
        return None, 0
    storm_sig, storm_count = _track_storm(cur_sig, storm_sig, storm_count)
    if storm_count >= 3 and approved:
        _inject_loop_guard(approved, storm_count)
    return storm_sig, storm_count


def _build_failure_signature(approved: list[tuple]) -> str | None:
    """如果所有工具结果都包含 error，返回签名；否则返回 None。"""
    sig_parts: list[str] = []
    for _item in approved:
        tool_name = _item[1]
        err_line = _extract_error_line(_item[4])
        if err_line is None:
            return None
        sig_parts.append(f"{tool_name}:{err_line}")
    return "|".join(sig_parts) if sig_parts else None


def _extract_error_line(result: str) -> str | None:
    """从工具结果中提取错误信息首行，无错误返回 None。"""
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and "error" in parsed:
            return str(parsed["error"]).split("\n")[0][:80]
    except (json.JSONDecodeError, Exception):
        pass
    return None


def _track_storm(cur_sig: str, storm_sig: str | None, storm_count: int) -> tuple[str | None, int]:
    """追踪连续错误签名：相同则递增，不同则重置。"""
    if cur_sig == storm_sig:
        return storm_sig, storm_count + 1
    return cur_sig, 1


def _inject_loop_guard(approved: list[tuple], storm_count: int) -> None:
    """在最后一条 approved 结果中注入 loop guard 警告。"""
    last = approved[-1]
    warn = (
        f"\n\n[loop guard] '{last[1]}' 已连续失败 {storm_count} 次"
        f"且错误相同。重复发送——即使换了措辞——也不会有帮助。换方法："
        f"如果参数被截断，拆成多个小调用；"
        f"否则修正参数、换工具、或在最终回复中说明障碍。"
    )
    last_result = last[4] + warn
    approved[-1] = (last[0], last[1], last[2], last[3], last_result)
    logger.warning("stormBreaker: %s 连续失败 %d 次，已注入 loop guard", last[1], storm_count)


def _handle_tool_results(
    ctx: CacheContext,
    tool_calls: list[dict],
    eager_results: dict[int, str],
    on_tool: ToolCallback,
    storm_sig: str | None,
    storm_count: int,
) -> tuple[str | None, int]:
    """解析工具调用 → 执行 → stormBreaker → 压缩 → 写 log。

    返回 (更新后的 storm_sig, 更新后的 storm_count)。
    """
    ctx.log.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

    approved: list[tuple] = []
    for tc_data in tool_calls:
        tool_name = tc_data["function"]["name"]
        try:
            args = json.loads(tc_data["function"]["arguments"])
        except json.JSONDecodeError:
            repaired = _repair_json(tc_data["function"]["arguments"])
            try:
                args = json.loads(repaired)
                logger.info("工具调用 JSON 已修复: %s", tool_name)
            except json.JSONDecodeError:
                error_payload = {"error": f"JSON 解析失败: {tc_data['function']['arguments'][:100]}"}
                dummy_tc = SimpleNamespace(function=SimpleNamespace(name=tool_name, arguments="{}"))
                approved.append((tc_data, tool_name, {}, dummy_tc, json.dumps(error_payload, ensure_ascii=False)))
                continue
        tc = SimpleNamespace(function=SimpleNamespace(name=tool_name, arguments=tc_data["function"]["arguments"]))
        on_tool(tool_name, args)
        approved.append((tc_data, tool_name, args, tc, None))

    # 注入 eager 预执行结果
    for i, eager_result in eager_results.items():
        if i < len(approved) and approved[i][4] is None:
            approved[i] = (approved[i][0], approved[i][1], approved[i][2], approved[i][3], eager_result)

    # 执行未跑的工具
    exec_indices = [i for i, item in enumerate(approved) if item[4] is None]
    exec_items = [approved[i] for i in exec_indices]
    if exec_items:
        exec_results = _execute_tool_batch(exec_items)
        for idx, result in zip(exec_indices, exec_results, strict=True):
            approved[idx] = (approved[idx][0], approved[idx][1], approved[idx][2], approved[idx][3], result)

    # stormBreaker
    storm_sig, storm_count = _check_storm_breaker(approved, storm_sig, storm_count)

    # 写 tool 结果到 log
    for tc_data, tool_name, _args, _tc, result in approved:
        result = truncate_to_budget(result, ctx.all)
        result = _compress_tool_result(tool_name, result)
        ctx.log.append({"role": "tool", "tool_call_id": tc_data["id"], "content": result, "_tool_name": tool_name})

    return storm_sig, storm_count


def run_conversation(
    ctx: CacheContext,
    user_input: str,
    on_token: TokenCallback,
    on_tool: ToolCallback,
    on_progress: Callable[[], None] | None = None,
    session_id: str = "",
) -> str:
    """处理一轮对话：自动路由 + 工具调用循环。

    循环终止条件：
      1. LLM 返回无 tool_calls（自然完成）
      2. 上下文 token 超限
      3. 单轮请求数达 _MAX_REQUESTS_PER_TURN（成本熔断）
      4. 用户 Esc 中断
    """
    # ── 运行时守卫：防止误传 list[dict] 等错误类型 ──
    if not hasattr(ctx, "log") or not hasattr(ctx, "all"):
        raise TypeError(
            f"run_conversation() 的 ctx 参数必须是 CacheContext 实例，"
            f"但收到了 {type(ctx).__name__}。"
            f"请确认传入了 CacheContext 而非 list[dict]。"
        )
    # ── 每轮刷新 volatile 上下文（strip-then-append，缓存安全挂尾） ──
    ctx.log[:] = [m for m in ctx.log if not m.get("_task_ctx")]
    from .tasks import make_task_context_message
    task_ctx = make_task_context_message()
    if task_ctx:
        ctx.log.append(task_ctx)
    # 追加用户输入到 log（prefix 不变）
    ctx.log.append({"role": "user", "content": user_input})
    # ── 教训注入：检查本轮任务是否匹配已知失败模式 ──
    from .lesson import inject_lesson_context
    inject_lesson_context(ctx.log, "_task", {"desc": user_input})

    # 重设 Storm 去重窗口 + SAFE 并行统计（同轮工具循环内）
    from .tools.storm import reset_storm
    reset_storm()
    # ── 感知层：设当前会话，工具调用统计自动归入 ──
    from .tracker import set_session as _tracker_set_session
    _tracker_set_session(session_id)

    reset_safe_stats()

    # 自动选择模型（始终 Pro）
    backend = auto_select_model(user_input)
    logger.info("路由: '%s...' → %s", user_input[:30], backend.info.name)

    requests_made = 0
    # ── stormBreaker：同轮连续同一错误 → 打破死亡螺旋 ──
    _storm_sig: str | None = None   # 上一轮失败签名
    _storm_count = 0                # 同一签名连续次数
    while True:
        if requests_made >= _MAX_REQUESTS_PER_TURN:
            return (
                f"本轮已发出 {requests_made} 次 API 请求，达到熔断上限"
                f"（CTG_MAX_REQUESTS_PER_TURN={_MAX_REQUESTS_PER_TURN}）。"
                "已停止，请检查任务是否陷入循环，或拆小后继续。"
            )
        used = count_messages_tokens(ctx.all)
        limit = int(MAX_CONTEXT_TOKENS * TOOL_LOOP_THRESHOLD)
        if used >= limit:
            return (
                f"上下文用量已达上限（{used}/{MAX_CONTEXT_TOKENS} tokens）。"
                "请开启新会话或精简问题。"
            )
        # 自动压缩旧对话（用量超过 _COMPACT_THRESHOLD 即触发）
        compact_limit = int(MAX_CONTEXT_TOKENS * _COMPACT_THRESHOLD)
        if used >= compact_limit:
            logger.info("触发上下文优化：%d >= %d (%.0f%%)", used, compact_limit, _COMPACT_THRESHOLD * 100)
            _compact_context(ctx, user_input)
            logger.info("压缩后消息数: %d", len(ctx))

        try:
            _skip_tail = _NO_VOLATILE_TAIL or ((requests_made > 0) and not _KEEP_TOOLLOOP_TAIL)
            content, tool_calls, eager_results = _invoke_llm_eager(
                backend, ctx.send(skip_volatile_system=_skip_tail), on_token, session_id,
            )
            requests_made += 1
        except UserInterruptError:
            clear_interrupt()
            return "\n\n[⏹️ 已中断]"
        if tool_calls:
            ctx.log.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            try:
                approved: list[tuple] = []  # (tc_data, tool_name, args, tc, pre_result_or_None)

                for tc_data in tool_calls:
                    tool_name = tc_data["function"]["name"]
                    try:
                        args = json.loads(tc_data["function"]["arguments"])
                    except json.JSONDecodeError:
                        repaired = _repair_json(tc_data["function"]["arguments"])
                        try:
                            args = json.loads(repaired)
                            logger.info("工具调用 JSON 已修复: %s", tool_name)
                        except json.JSONDecodeError:
                            error_payload = {
                                "error": f"JSON 解析失败: {tc_data['function']['arguments'][:100]}",
                            }
                            approved.append((
                                tc_data,
                                tool_name,
                                {},
                                SimpleNamespace(function=SimpleNamespace(name=tool_name, arguments="{}")),
                                json.dumps(error_payload, ensure_ascii=False),
                            ))
                            continue
                    tc = SimpleNamespace(
                        function=SimpleNamespace(
                            name=tool_name,
                            arguments=tc_data["function"]["arguments"],
                        )
                    )
                    on_tool(tool_name, args)
                    approved.append((tc_data, tool_name, args, tc, None))

                # ── 注入 eager 预执行结果（LLM 流式期间已跑完的工具）──
                for i, eager_result in eager_results.items():
                    if i < len(approved) and approved[i][4] is None:
                        tc_data = approved[i][0]
                        tname = approved[i][1]
                        targs = approved[i][2]
                        tc = approved[i][3]
                        approved[i] = (tc_data, tname, targs, tc, eager_result)

                # 同轮去重由 storm.py 在 execute_tool 豁口统一处理（单一机制）
                exec_indices: list[int] = []
                exec_items: list[tuple] = []
                for i, item in enumerate(approved):
                    if item[4] is None:  # pre_result is None → 需要执行
                        exec_indices.append(i)
                        exec_items.append(item)

                if exec_items:
                    exec_results = _execute_tool_batch(exec_items)
                    for idx, result in zip(exec_indices, exec_results, strict=True):
                        tc_data, tool_name, args, tc, _ = approved[idx]
                        approved[idx] = (tc_data, tool_name, args, tc, result)

                # ── stormBreaker：检测同轮连续同一错误 ──
                _all_failed = True
                _sig_parts = []
                for _item in approved:
                    _tool_name = _item[1]
                    _result = _item[4]
                    _has_err = False
                    _err_line = ""
                    try:
                        _parsed = json.loads(_result)
                        if isinstance(_parsed, dict) and "error" in _parsed:
                            _has_err = True
                            _err_line = str(_parsed["error"]).split("\n")[0][:80]
                    except (json.JSONDecodeError, Exception):
                        pass
                    if not _has_err:
                        _all_failed = False
                        break
                    _sig_parts.append(f"{_tool_name}:{_err_line}")
                if _all_failed and _sig_parts:
                    _cur_sig = "|".join(_sig_parts)
                    if _cur_sig == _storm_sig:
                        _storm_count += 1
                    else:
                        _storm_sig = _cur_sig
                        _storm_count = 1
                    if _storm_count >= 3 and approved:
                        _last = approved[-1]
                        _warn = (
                            f"\n\n[loop guard] '{_last[1]}' 已连续失败 {_storm_count} 次"
                            f"且错误相同。重复发送——即使换了措辞——也不会有帮助。换方法："
                            f"如果参数被截断，拆成多个小调用；"
                            f"否则修正参数、换工具、或在最终回复中说明障碍。"
                        )
                        _last_result = _last[4] + _warn
                        approved[-1] = (_last[0], _last[1], _last[2], _last[3], _last_result)
                        logger.warning(
                            "stormBreaker: %s 连续失败 %d 次，已注入 loop guard",
                            _last[1], _storm_count,
                        )
                else:
                    _storm_sig = None
                    _storm_count = 0

                for tc_data, tool_name, _args, _tc, result in approved:
                    result = truncate_to_budget(result, ctx.all)
                    result = _compress_tool_result(tool_name, result)
                    ctx.log.append({
                        "role": "tool",
                        "tool_call_id": tc_data["id"],
                        "content": result,
                        "_tool_name": tool_name,
                    })
                # 记忆已移入缓存前缀（会话开始快照、会话内不变），不在轮内更新——
                # 中途 remember 的新记忆靠对话上下文带过本会话，落盘后下次会话进前缀索引。
                # 钉板变更后刷新 ctx.log 中的钉板消息（轮内 pin/unpin 即时生效，缓存安全挂尾）
                from .session_pins import is_pinboard_msg, render_tail
                pin_idx = next((i for i, m in enumerate(ctx.log) if is_pinboard_msg(m)), -1)
                pin_content = render_tail()
                if pin_content and pin_idx >= 0:
                    ctx.log[pin_idx] = {"role": "system", "content": pin_content, "_volatile": True}
                elif pin_content and pin_idx < 0:
                    ctx.log.append({"role": "system", "content": pin_content, "_volatile": True})
                elif not pin_content and pin_idx >= 0:
                    ctx.log.pop(pin_idx)  # 全 unpin 后移除空钉板消息
                # on_progress 不在循环内调用——移到最后只调用一次
            except Exception:
                # 异常时补上 tool 结果消息，防止下次 API 调用因缺少 tool 消息而 400
                for tc_data in tool_calls:
                    already_saved = any(
                        m.get("role") == "tool" and m.get("tool_call_id") == tc_data["id"]
                        for m in ctx.log
                    )
                    if not already_saved:
                        ctx.log.append({
                            "role": "tool",
                            "tool_call_id": tc_data["id"],
                            "content": json.dumps({"error": "工具处理异常，已跳过"}, ensure_ascii=False),
                        })
                raise
        else:
            ctx.log.append({"role": "assistant", "content": content or ""})
            # 长任务的"接着做"不在这里硬续（旧补丁靠 has_unfinished 注入"不要停"=
            # 与对话回合打架、逼出自问自答）。改由 main 的 REPL 在"agent 这轮真推进了
            # current.md"时自主驱动下一步（task_loop.run_task_continuation）——续跑条件
            # 是 agent 自己的推进、不是任务存在，停由它的判断（停止推进/标 [!]）决定。
            # ── 建任务建议：干了不少活却没 current.md 任务 → 提示建任务（一次/会话）──
            # 事实触发（请求数+无任务），判断留 agent（带 opt-out），不机械分类长任务。
            from .tasks import maybe_suggest_task_nudge
            _suggest = maybe_suggest_task_nudge(requests_made, _TASK_SUGGEST_MIN_REQUESTS)
            if _suggest:
                ctx.log.append({"role": "system", "content": _suggest, "_volatile": True})
            if on_progress:
                on_progress()
            from .tasks import get_task_progress_line
            progress = get_task_progress_line()
            if progress:
                on_token("\n" + progress + "\n")
            return content or ""
