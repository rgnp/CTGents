"""LLM 后端抽象：多模型支持、自动路由、流式调用。"""

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from .cache_context import CacheContext
from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    FLASH_MAX_TOKENS,
    MAX_CONTEXT_TOKENS,
    MAX_RETRIES,
    MODEL_FLASH,
    MODEL_PRO,
    PRO_MAX_TOKENS,
    RETRY_BASE_DELAY,
    TOOL_LOOP_THRESHOLD,
)
from .tools import execute_tool, get_tools, is_plan_mode, set_plan_mode

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

    id: str                    # 模型 ID，如 deepseek-v4-flash
    name: str                  # 显示名称，如 "Flash"
    provider: str = "deepseek" # 提供商
    supports_tools: bool = True   # 是否支持 function calling
    supports_stream: bool = True  # 是否支持流式
    supports_thinking: bool = False # 是否有思考过程
    max_tokens: int = 8192     # 最大输出 token


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
    ) -> tuple[str | None, list[dict] | None]:
        """非流式调用。返回 (content, tool_calls)。"""
        ...

    def get_model_info(self) -> ModelInfo:
        return self.info


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
    ) -> tuple[str | None, list[dict] | None]:
        content_parts: list[str] = []
        tool_calls: list[dict] = []

        kwargs = {
            "model": self.info.id,
            "messages": messages,
            "stream": True,
            "max_tokens": self.info.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if tools and self.info.supports_tools:
            kwargs["tools"] = tools

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

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    on_token(delta.content)
                    content_parts.append(delta.content)

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
        except UserInterruptError:
            # 中断时返回已收到的部分内容，但不作为完整回复
            content = "".join(content_parts) if content_parts else None
            if content:
                on_token("\n\n[⚠️ 已中断 — 以上是已收到的部分回复]\n")
            raise  # 重新抛出，让 run_conversation 处理消息清理

        # 流式正常结束
        content = "".join(content_parts) if content_parts else None
        final_tool_calls = tool_calls if tool_calls else None
        return content, final_tool_calls

    def chat_non_stream(
        self,
        messages: list[dict],
        on_token: TokenCallback,
        tools: list[dict] | None = None,
    ) -> tuple[str | None, list[dict] | None]:
        kwargs = {
            "model": self.info.id,
            "messages": messages,
            "max_tokens": self.info.max_tokens,
        }
        if tools and self.info.supports_tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)

        # 捕获 API 返回的真实 usage（含 cache hit/miss）
        if hasattr(response, "usage") and response.usage:
            _set_api_usage(self.info.name.lower(), response.usage)

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

        if msg.content:
            on_token(msg.content)

        return msg.content, tool_calls


# ═══════════════════════════════════════════════════════════════
# 模型注册表
# ═══════════════════════════════════════════════════════════════

# 所有可用模型
AVAILABLE_MODELS: dict[str, LLMBackend] = {
    "flash": DeepSeekBackend(ModelInfo(
        id=MODEL_FLASH,
        name="Flash",
        provider="deepseek",
        supports_tools=True,
        supports_stream=True,
        supports_thinking=False,
        max_tokens=FLASH_MAX_TOKENS,
    )),
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

# 当前模型 — 始终 Pro
_current_backend: LLMBackend = AVAILABLE_MODELS["pro"]

# ── 模型切换滞后计数器（避免 flash↔pro ping-pong 破坏前缀缓存） ──
# DeepSeek KV 缓存按模型独立 — flash 和 pro 各有自己的缓存。
# 频繁切换 = 两个模型都攒不起长前缀，缓存利用率暴跌。
# 策略：升 pro 要快（复杂任务不能降质），降 flash 要慢（等缓存养肥）。
_consecutive_simple_tasks: int = 0
_STICKY_THRESHOLD: int = 3   # 连续 N 轮简单任务才从 pro 降回 flash


def get_current_backend() -> LLMBackend:
    return _current_backend


def get_current_model_name() -> str:
    """返回当前模型的显示名称。"""
    return _current_backend.info.name


def get_current_model_id() -> str:
    """返回当前模型的 API ID。"""
    return _current_backend.info.id


def switch_model(name: str) -> tuple[bool, str]:
    """切换当前模型。name 可以是 'flash'、'pro' 或完整模型 ID。"""
    global _consecutive_simple_tasks
    _consecutive_simple_tasks = 0  # 显式切换重置滞后计数

    # 先按短名称查找
    if name in AVAILABLE_MODELS:
        global _current_backend
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

# 空统计模板
_EMPTY_STATS = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                 "cache_hit_tokens": 0, "cache_miss_tokens": 0}

# 当前会话 ID 和内存中的统计（切换会话时自动读写文件）
_current_session_id: str = ""
_CACHE_STATS: dict[str, dict] = {
    "flash": dict(_EMPTY_STATS),
    "pro": dict(_EMPTY_STATS),
}


# API 返回的真实 usage（运行时数据，不持久化）
_last_api_usage: dict[str, dict | None] = {"flash": None, "pro": None}


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
                return {"flash": dict(_EMPTY_STATS), "pro": dict(_EMPTY_STATS)}
            for model in ("flash", "pro"):
                if model not in data:
                    data[model] = dict(_EMPTY_STATS)
            return data
    except Exception:
        pass
    return {
        "flash": dict(_EMPTY_STATS),
        "pro": dict(_EMPTY_STATS),
    }


def _save_cache_stats(session_id: str) -> None:
    """将当前内存统计写入指定会话的文件。"""
    try:
        _STATS_DIR.mkdir(parents=True, exist_ok=True)
        p = _stats_path(session_id)
        p.write_text(json.dumps(_CACHE_STATS, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    except Exception:
        pass  # 写入失败不阻塞


def _ensure_session(session_id: str) -> None:
    """确保内存中是目标会话的统计。如果会话切换，自动保存旧会话、加载新会话。"""
    global _current_session_id, _CACHE_STATS

    if not session_id:
        return
    if session_id == _current_session_id:
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
    import contextlib
    with contextlib.suppress(Exception):
        _last_api_usage[model_key] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
            "cache_miss_tokens": getattr(usage, "prompt_cache_miss_tokens", 0) or 0,
        }


def _update_cache_stats(model_key: str, messages: list[dict], session_id: str = "") -> None:
    """每次 API 请求后更新当前会话的缓存统计，并持久化。

    优先使用 API 返回的真实 cache_hit/cache_miss 数据，
    session_id 用于隔离不同会话的统计数据。
    """
    global _CACHE_STATS

    _ensure_session(session_id)

    stats = _CACHE_STATS.setdefault(model_key, dict(_EMPTY_STATS))
    stats["requests"] += 1

    # 优先用 API 返回的真实数据
    usage = _last_api_usage.get(model_key)
    if usage and usage.get("prompt_tokens", 0) > 0:
        stats["prompt_tokens"] += usage["prompt_tokens"]
        stats["completion_tokens"] += usage["completion_tokens"]
        stats["cache_hit_tokens"] += usage["cache_hit_tokens"]
        stats["cache_miss_tokens"] += usage["cache_miss_tokens"]
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
        data = {"flash": dict(_EMPTY_STATS), "pro": dict(_EMPTY_STATS)}

    models: dict[str, dict] = {}
    total = dict(_EMPTY_STATS)
    for key, stats in data.items():
        if key in ("flash", "pro") and isinstance(stats, dict):
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
) -> tuple[str | None, list[dict] | None]:
    """调用 LLM，带重试机制。首次流式，失败后降级为非流式重试。"""
    model_key = backend.info.name.lower()
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


# ═══════════════════════════════════════════════════════════════
# API 消息构建（前缀缓存友好版）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 工具结果压缩（Phase 2：缓存优化）
# ═══════════════════════════════════════════════════════════════

# 压缩阈值（字符数，约等于 token 数）
_TOOL_RESULT_COMPRESS_THRESHOLD = 1200


def _compress_tool_result(tool_name: str, result: str) -> str:
    """压缩过大的工具结果，减少后续轮次的 token 消耗。

    策略：
    - read_file / read_file_lines → 不压缩（仅受 token 预算动态截断）
    - 小于阈值 → 不压缩
    - 大于阈值 → 保留前 N 字符 + 截断提示
    """
    if tool_name in _SKIP_COMPRESS_TOOLS or len(result) <= _TOOL_RESULT_COMPRESS_THRESHOLD:
        return result

    head = result[:_TOOL_RESULT_COMPRESS_THRESHOLD]
    total = len(result)

    if tool_name in ("search_web", "read_page"):
        hint = (
            f"\n\n[已压缩：搜索结果 {total} 字符，仅显示前 {_TOOL_RESULT_COMPRESS_THRESHOLD} 字符。"
            f"如需完整内容，可重新搜索或阅读页面]"
        )
    else:
        hint = (
            f"\n\n[已压缩：原始结果 {total} 字符，仅显示前 {_TOOL_RESULT_COMPRESS_THRESHOLD} 字符]"
        )

    return head + hint

# ═══════════════════════════════════════════════════════════════
# 对话上下文优化（Append-Only — 对齐 Reasonix 缓存优先策略）
# ═══════════════════════════════════════════════════════════════

# 触发滑窗压缩的上下文比例（达到 80% 时触发 — 驱旧消息，换出空间）
_COMPACT_THRESHOLD = 0.65
# 保留比例：滑窗压缩后保留最近 N% 的消息
_COMPACT_KEEP_RATIO = 0.40
# 工具结果清理门槛：上下文用量低于此比例时不清理，保留工具结果以维持前缀缓存连续。
# 清理会删改 log 中间消息（断缓存），仅当上下文够大、收益(甩掉臃肿工具结果)>代价时才做。
# 贴近压缩点 0.65：清理是压缩前的轻量步骤，过早触发会每轮断缓存（首要目标是缓存命中）。
_CLEANUP_CONTEXT_THRESHOLD = 0.60
# 一轮内工具结果达到此数量才考虑清理（太少不值得断缓存）
_CLEANUP_MIN_TOOL_RESULTS = 2
# 话题切换关键词（检测到后追加边界标记）
_TOPIC_SWITCH_KEYWORDS = [
    "换个", "换一", "不谈", "不说", "跳过", "算了",
    "下一个", "新的", "另外", "不管", "再看",
    "topic", "switch", "next", "another", "skip",
]


def _is_topic_switch(user_input: str) -> bool:
    """检测用户输入是否包含换话题信号。"""
    text = user_input.lower().strip()
    return any(kw.lower() in text for kw in _TOPIC_SWITCH_KEYWORDS)


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


def _cleanup_tool_results(ctx) -> None:
    """任务完成后压缩中间工具结果：保留摘要，丢弃细节。

    每轮任务（从用户输入到 LLM 最终回复）完成后调用。
    工具结果占 log 膨胀的绝大部分——任务完成后它们不再有用，
    替换为一行摘要可以在下次滑窗压缩前有效控制 log 大小。

    门槛：仅当上下文用量 ≥ _CLEANUP_CONTEXT_THRESHOLD 时才清理。清理会删改 log
    中间消息、断掉 DeepSeek 前缀缓存连续；短对话保留工具结果维持缓存命中（首要目标），
    只有上下文真正变大、"甩掉臃肿工具结果"的收益超过"断一次缓存"的代价时才动手。
    """
    # 门槛 1：上下文还小 → 保留工具结果，维持缓存连续
    if count_messages_tokens(ctx.all) < MAX_CONTEXT_TOKENS * _CLEANUP_CONTEXT_THRESHOLD:
        return

    log = ctx.log
    # 找到最后一次 user 消息
    last_user = None
    for i in range(len(log) - 1, -1, -1):
        if log[i].get("role") == "user":
            last_user = i
            break
    if last_user is None:
        return

    tool_names: list[str] = []
    tool_indices: list[int] = []

    for i in range(last_user + 1, len(log)):
        m = log[i]
        if m.get("role") == "tool" and m.get("_tool_result_compressed"):
            name = m.get("_tool_name", "?")
            if name not in tool_names:
                tool_names.append(name)
            tool_indices.append(i)

    if len(tool_indices) < _CLEANUP_MIN_TOOL_RESULTS:
        return  # 太少，不值得断缓存压缩

    summary = f"⏪ 已归档 {len(tool_indices)} 条工具结果: {', '.join(tool_names[:10])}"
    if len(tool_names) > 10:
        summary += f" 等共 {len(tool_names)} 种工具"

    # 找到发起这些工具调用的所有 assistant 消息，去掉 tool_calls 字段
    deleted_call_ids = {
        log[idx].get("tool_call_id", "")
        for idx in tool_indices
    }
    for i in range(last_user, len(log)):
        m = log[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tc_ids = {tc.get("id", "") for tc in m["tool_calls"]}
            if tc_ids and tc_ids <= deleted_call_ids:
                if m.get("content"):
                    m.pop("tool_calls", None)
                else:
                    log[i] = {"role": "assistant", "content": summary}

    # 第一条工具结果替换为摘要，其余删除
    first_idx = tool_indices[0]
    log[first_idx] = {"role": "system", "content": summary, "_volatile": True}
    for idx in reversed(tool_indices[1:]):
        del log[idx]

    logger.info("工具结果清理：%d → 1 条（%d 种工具）", len(tool_indices), len(tool_names))


def _compact_cache_context(ctx, user_input: str, force: bool = False) -> None:
    """滑窗压缩：超阈值时驱旧消息，替换为摘要。

    固定前缀保持不变（保障 DeepSeek 缓存命中）。
    log 区旧消息被摘要替代，释放空间供新对话继续。
    force=True 时无视 65% 门槛，直接压缩（手动 /compact）。
    """
    from .tools.tokens import count_messages_tokens

    log = ctx.log
    if not log:
        return

    # 估算当前 token 用量
    all_msgs = ctx.prefix + log
    used = count_messages_tokens(all_msgs)
    limit = MAX_CONTEXT_TOKENS * _COMPACT_THRESHOLD

    if not force and used < limit and not _is_topic_switch(user_input):
        return  # 不需要压缩

    # ── 话题切换：加标记，不驱旧 ──
    if _is_topic_switch(user_input):
        brief = _make_brief_summary(log, max_len=200)
        if brief:
            log.append({
                "role": "system",
                "content": f"⏪ 前一话题已结束。{brief}",
            })
            logger.info("话题切换：标记边界（log 共 %d 条）", len(log))
        return

    # ── 滑窗压缩：驱旧消息 ──
    keep_start = int(len(log) * _COMPACT_KEEP_RATIO)
    if keep_start < 2:
        return  # log 太短，不压缩

    evicted = log[:keep_start]
    kept = log[keep_start:]

    summary = _make_brief_summary(evicted, max_len=500)
    if not summary:
        return

    # 替换：摘要 + 保留的消息
    new_log = [{
        "role": "system",
        "content": f"⏪ 对话归档：{summary}（已驱 {len(evicted)} 条旧消息）",
    }]
    new_log.extend(kept)

    # 检查是否有遗留的 volatile 内存消息，移到新 log 最前面
    for m in evicted:
        if m.get("_volatile") and "记忆" in m.get("content", ""):
            new_log.insert(0, m)
            break

    ctx.log.clear()
    ctx.log.extend(new_log)
    logger.info(
        "滑窗压缩：驱 %d 条旧消息，保留 %d 条（释放约 %d%% 空间）",
        len(evicted), len(kept), int((1 - _COMPACT_KEEP_RATIO) * 100),
    )


def _make_brief_summary(messages: list[dict], max_len: int = 300) -> str:
    """从对话列表中提取关键信息做摘要，不调用 LLM。

    提取策略：取最初几轮的用户问题 + 关键助手回复片段。
    """
    parts: list[str] = []
    user_count = 0

    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            user_count += 1
            # 取用户问题的前 60 个字符作为线索
            snippet = content[:60].replace("\n", " ")
            if user_count <= 5 and len("".join(parts)) < max_len:
                parts.append(snippet)
        elif m.get("role") == "tool" and m.get("_tool_name") in ("search_web", "read_page"):
            # 提取搜索结果的关键词
            first_line = content.split("\n")[0][:40] if content else ""
            if first_line and len("".join(parts)) < max_len:
                parts.append(f"[搜索] {first_line}")

    if not parts:
        return ""

    result = "、".join(parts)
    if len(result) > max_len:
        result = result[:max_len] + "…"

    if user_count > 5:
        result += f"（共 {user_count} 轮交互）"

    return result


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


def _execute_tool_batch(approved: list[tuple]) -> list[str]:
    """按序分块并行执行工具 — 保留 LLM 原始调用顺序。

    连续 SAFE 工具 → 并行（同批内顺序无关）
    非 SAFE 工具 → 等待前一批完成后单独执行
    确保 [read(A), run_cmd(改A), read(A)] 中第二个 read 看到改后的 A。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
            # 非 SAFE：串行执行
            tc = approved[i][3]
            try:
                results[i] = execute_tool(tc)
            except Exception as e:
                results[i] = json.dumps({"error": f"执行失败: {e}"}, ensure_ascii=False)
            total_serial += 1
            i += 1
        else:
            # 收集连续 SAFE 工具成一批
            batch: list[int] = []
            while i < n:
                if approved[i][4] is not None:
                    i += 1
                    continue
                if approved[i][1] in _PARALLEL_SAFE:
                    batch.append(i)
                    i += 1
                else:
                    break

            if batch:
                names = [approved[j][1] for j in batch]
                print(f"  ⚡ [SAFE] 并行执行 {len(batch)} 个工具: {', '.join(names)}")
                with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as pool:
                    fut_map: dict = {}
                    for j in batch:
                        tc = approved[j][3]
                        fut = pool.submit(execute_tool, tc)
                        fut_map[fut] = j
                    for fut in as_completed(fut_map):
                        idx = fut_map[fut]
                        try:
                            results[idx] = fut.result()
                        except Exception as e:
                            results[idx] = json.dumps({"error": f"并行执行失败: {e}"}, ensure_ascii=False)
                total_parallel += len(batch)

    _update_safe_stats(total_parallel, total_serial)
    return results


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


# ═══════════════════════════════════════════════════════════════
# 自动 Plan Mode — 长任务默认只读分析（LLM 可在 think 中推翻）
# ═══════════════════════════════════════════════════════════════

_AUTO_PLAN_MIN_CHARS = 300  # 超长描述意味着任务需要理解现状


def _should_auto_plan(user_input: str) -> bool:
    """纯长度启发式，不做关键词匹配。LLM 若认为不需要可用 think 自行退出。"""
    return len(user_input) >= _AUTO_PLAN_MIN_CHARS


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
      3. 连续 3 轮无用户可见进展（兜底熔断）
    """
    # ── 运行时守卫：防止误传 list[dict] 等错误类型 ──
    if not hasattr(ctx, "log") or not hasattr(ctx, "all"):
        raise TypeError(
            f"run_conversation() 的 ctx 参数必须是 CacheContext 实例，"
            f"但收到了 {type(ctx).__name__}。"
            f"请确认传入了 CacheContext 而非 list[dict]。"
        )
    # 追加用户输入到 log（prefix 不变）
    ctx.log.append({"role": "user", "content": user_input})

    # 重设 Storm 去重窗口 + SAFE 并行统计（同轮工具循环内）
    from .tools.storm import reset_storm
    reset_storm()
    reset_safe_stats()

    # 自动选择模型
    prev_backend = _current_backend  # 切换前快照（auto_select_model 会更新 _current_backend）
    backend = auto_select_model(user_input)
    model_name = backend.info.name
    logger.info("路由: '%s...' → %s", user_input[:30], model_name)

    # ── 自动 Plan Mode：复杂任务先只读分析 ──
    auto_plan = False
    if _should_auto_plan(user_input) and not is_plan_mode():
        set_plan_mode(True)
        auto_plan = True
        on_token("📋 任务较复杂，先进入只读分析…\n\n")

    # 模型变了 → 通知用户
    if backend is not prev_backend:
        on_token(f"\n[🤖 使用 {model_name} 处理此任务]\n\n")

    while True:
        used = count_messages_tokens(ctx.all)
        limit = int(MAX_CONTEXT_TOKENS * TOOL_LOOP_THRESHOLD)
        if used >= limit:
            return (
                f"上下文用量已达上限（{used}/{MAX_CONTEXT_TOKENS} tokens）。"
                "请开启新会话或精简问题。"
            )
        # Phase 3：自动压缩旧对话（超过 70% 时触发）
        compact_limit = int(MAX_CONTEXT_TOKENS * _COMPACT_THRESHOLD)
        if used >= compact_limit:
            logger.info("触发上下文优化：%d >= %d (%.0f%%)", used, compact_limit, _COMPACT_THRESHOLD * 100)
            _compact_context(ctx, user_input)
            logger.info("压缩后消息数: %d", len(ctx))

        try:
            content, tool_calls = _invoke_llm(backend, ctx.send(), on_token, session_id)
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

                for tc_data, tool_name, _args, _tc, result in approved:
                    result = truncate_to_budget(result, ctx.all)
                    result = _compress_tool_result(tool_name, result)
                    ctx.log.append({
                        "role": "tool",
                        "tool_call_id": tc_data["id"],
                        "content": result,
                        "_tool_name": tool_name,
                        "_tool_result_compressed": True,  # 标记可压缩
                    })
                # 记忆变更后更新 ctx.log 中的记忆索引
                from .tools.memory import clear_dirty, get_context, is_dirty
                if is_dirty():
                    old_idx = next((i for i, m in enumerate(ctx.log)
                                    if m.get("role") == "system" and "你拥有以下记忆" in m.get("content","")), -1)
                    new_ctx = get_context()
                    if new_ctx and old_idx >= 0:
                        ctx.log[old_idx] = {"role": "system", "content": new_ctx, "_volatile": True}
                    elif new_ctx and old_idx < 0:
                        ctx.log.append({"role": "system", "content": new_ctx, "_volatile": True})
                    clear_dirty()
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
            if on_progress:
                on_progress()
            if auto_plan:
                set_plan_mode(False)
            _cleanup_tool_results(ctx)
            return content or ""
