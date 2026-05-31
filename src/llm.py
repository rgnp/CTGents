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
from .tools import execute_tool, get_tools
from .tools.tokens import count_messages_tokens, truncate_to_budget
from .cache_context import CacheContext


# 工具显示标签（用于安全确认提示，避免循环导入 main.py）
_TOOL_LABEL_MAP: dict[str, str] = {
    "search_web":    "搜索",
    "read_page":     "阅读网页",
    "read_file":     "读取文件",
    "read_file_lines": "读取文件（带行号）",
    "write_file":    "写入文件",
    "edit_file_lines": "行级编辑",
    "undo_edit":     "撤销编辑",
    "list_files":    "浏览目录",
    "delete_file":   "删除文件",
    "count_lines":   "统计行数",
    "run_python":    "执行代码",
    "run_command":   "执行命令",
    "grep_code":     "搜索代码",
    "think":         "思考",
    "remember":      "记住",
    "recall":        "回忆",
    "forget":        "忘记",
    "install_plugin": "安装插件",
    "list_plugins":   "列出插件",
    "discover":       "能力扫描",
    "plugin_spec":    "插件规范",
    "git_status":    "Git 状态",
    "git_diff":      "Git 差异",
    "git_log":       "Git 日志",
    "git_commit":    "Git 提交",
    "git_push":      "Git 推送",
    "git_pr":        "Git PR",
    "git_branch":    "Git 分支",
    "scan_project":  "扫描项目",
    "docs_sync_check": "文档同步检查",
}

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


class UserInterrupt(Exception):
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
                    raise UserInterrupt("用户按 Esc 中断")

                # 有的 API 在末 chunk 携带 usage（choices 为空），防御性处理
                if not chunk.choices:
                    if hasattr(chunk, "usage") and chunk.usage:
                        _set_api_usage(self.info.name.lower(), chunk.usage)
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
        except UserInterrupt:
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

# 当前选中的模型（默认 Flash）
_current_backend: LLMBackend = AVAILABLE_MODELS["flash"]


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
    # 先按短名称查找
    if name in AVAILABLE_MODELS:
        global _current_backend
        _current_backend = AVAILABLE_MODELS[name]
        info = _current_backend.info
        return True, f"已切换到 {info.name}（{info.id}）"

    # 按完整模型 ID 查找
    for _key, backend in AVAILABLE_MODELS.items():
        if backend.info.id == name:
            global _current_backend2
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
# 任务路由
# ═══════════════════════════════════════════════════════════════

# 简单任务关键词（用 Flash 处理）
_SIMPLE_TASK_KEYWORDS = [
    "查看", "读取", "搜索", "查找", "列出", "显示", "统计",
    "状态", "日志", "分支", "date", "time", "谁", "什么",
    "read", "show", "list", "status", "log", "find", "search",
    "git_status", "git_log", "git_branch", "list_files", "read_file",
    "scan_project", "git_diff",
]

# 复杂任务关键词（用 Pro 处理）
_COMPLEX_TASK_KEYWORDS = [
    "写", "创建", "修改", "重构", "优化", "调试", "设计",
    "实现", "修复", "重构", "部署", "架构", "分析",
    "write", "create", "implement", "refactor", "optimize",
    "debug", "fix", "design", "deploy", "重构", "架构",
    "git_commit", "git_push",
]


def _estimate_task_complexity(user_input: str) -> str:
    """根据用户输入预估任务复杂度，返回 'flash' 或 'pro'。"""
    text = user_input.lower()

    # 如果涉及写代码/修改/重构，用 Pro
    for kw in _COMPLEX_TASK_KEYWORDS:
        if kw.lower() in text:
            return "pro"

    # 如果只是查看/读取，用 Flash
    for kw in _SIMPLE_TASK_KEYWORDS:
        if kw.lower() in text:
            return "flash"

    # 如果包含代码块或较长文本，用 Pro
    if "```" in text or len(user_input) > 200:
        return "pro"

    # 默认用 Flash（省钱）
    return "flash"


def auto_select_model(user_input: str) -> LLMBackend:
    """根据用户输入自动选择最合适的模型。"""
    model_key = _estimate_task_complexity(user_input)
    selected = AVAILABLE_MODELS.get(model_key, _current_backend)
    return selected


# ── 缓存统计（按会话隔离 + 持久化到文件，不串会话） ──

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

# 上一次请求的 messages 签名（运行时数据，不持久化）
_cache_last_sig: dict[str, str] = {"flash": "", "pro": ""}

# API 返回的真实 usage（运行时数据，不持久化）
_last_api_usage: dict[str, dict | None] = {"flash": None, "pro": None}


def _stats_path(session_id: str) -> Path:
    """返回指定会话的统计文件路径。"""
    return _STATS_DIR / f"{session_id}.json"


def _load_cache_stats(session_id: str) -> dict[str, dict]:
    """从文件加载指定会话的统计，文件不存在则返回空统计。"""
    try:
        p = _stats_path(session_id)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
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
    global _current_session_id, _CACHE_STATS, _cache_last_sig

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
    _cache_last_sig = {"flash": "", "pro": ""}  # 签名不跨会话


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
    global _CACHE_STATS, _cache_last_sig

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

    # 后备方案：通过消息签名对比估算
    from .tools.tokens import count_messages_tokens
    total = count_messages_tokens(messages)
    stats["prompt_tokens"] += total

    sig_parts: list[str] = []
    for m in messages:
        sig_parts.append(m.get("role", "") + ":" + str(len(m.get("content", "") or "")))
    sig = "\n".join(sig_parts)
    last_sig = _cache_last_sig.get(model_key, "")

    if last_sig:
        prev_lines = last_sig.split("\n")
        curr_lines = sig.split("\n")
        match_count = 0
        for pl, cl in zip(prev_lines, curr_lines, strict=False):
            if pl == cl:
                match_count += 1
            else:
                break
        if match_count > 0:
            hit = int(total * match_count / max(len(curr_lines), 1))
            stats["cache_hit_tokens"] += hit
            stats["cache_miss_tokens"] += total - hit
        else:
            stats["cache_miss_tokens"] += total
    else:
        stats["cache_miss_tokens"] += total
    _cache_last_sig[model_key] = sig
    _save_cache_stats(_current_session_id)


def get_cache_stats(session_id: str = "") -> dict:
    """返回指定会话的缓存命中统计，供 /context 使用。

    传 session_id 查其他会话，不传则返回当前会话。
    返回值: {"models": {"flash": {...}, "pro": {...}}, "total": {...}}
    """
    if session_id and session_id != _current_session_id:
        data = _load_cache_stats(session_id)
    else:
        data = _CACHE_STATS

    models: dict[str, dict] = {}
    total = dict(_EMPTY_STATS)
    for key, stats in data.items():
        if key in ("flash", "pro"):
            models[key] = dict(stats)
            for k in total:
                total[k] += stats[k]
    return {"models": models, "total": total}
# 对话循环（核心）
# ═══════════════════════════════════════════════════════════════


def _invoke_llm(
    backend: LLMBackend,
    messages: list[dict],
    on_token: TokenCallback,
    session_id: str = "",
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

# 不压缩的工具（这些工具的结果通常短小或结构重要）
# 全部工具都压缩，无例外


def _compress_tool_result(tool_name: str, result: str) -> str:
    """压缩过大的工具结果，减少后续轮次的 token 消耗。

    策略：
    - 小于阈值 → 不压缩
    - 大于阈值 → 保留前 N 字符 + 截断提示
    - 特定工具（read_file 等）→ 额外提示可重新读取
    """
    if len(result) <= _TOOL_RESULT_COMPRESS_THRESHOLD:
        return result

    head = result[:_TOOL_RESULT_COMPRESS_THRESHOLD]
    total = len(result)

    # 根据工具类型决定提示语
    if tool_name in ("read_file", "read_file_lines"):
        hint = (
            f"\n\n[已压缩：原始结果 {total} 字符，仅显示前 {_TOOL_RESULT_COMPRESS_THRESHOLD} 字符。"
            f"如需完整内容，可使用 read_file 重新读取]"
        )
    elif tool_name in ("search_web", "read_page"):
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
# 对话历史压缩（Phase 3）
# ═══════════════════════════════════════════════════════════════

# 触发压缩的上下文比例（达到 25% 时触发，保持上下文紧凑）
_COMPACT_THRESHOLD = 0.25
# 压缩时保留最近对话的预算比例
_COMPACT_RETAIN_RATIO = 0.08
# 话题切换关键词（检测到后全量压缩）
_TOPIC_SWITCH_KEYWORDS = [
    "换个", "换一", "不谈", "不说", "跳过", "算了",
    "下一个", "新的", "另外", "不管", "再看",
    "topic", "switch", "next", "another", "skip",
]


def _is_topic_switch(user_input: str) -> bool:
    """检测用户输入是否包含换话题信号。"""
    text = user_input.lower().strip()
    return any(kw.lower() in text for kw in _TOPIC_SWITCH_KEYWORDS)


def _compact_context(ctx, user_input: str):
    """In-place compaction. Returns None for CacheContext, list for legacy flat list.

    Accepts CacheContext (preferred) or legacy flat list[dict].
    """
    from .cache_context import CacheContext

    if isinstance(ctx, CacheContext):
        _compact_cache_context(ctx, user_input)
    else:
        # Legacy: flat list — build temp CacheContext, compact, return result
        prefix = [m for m in ctx if m.get("role") == "system"]
        log = [m for m in ctx if m.get("role") != "system"]
        tmp = CacheContext(prefix_msgs=prefix, log_msgs=log)
        _compact_cache_context(tmp, user_input)
        return tmp.all


def _compact_cache_context(ctx, user_input: str) -> None:
    """Internal: compact CacheContext.log in-place."""
    log = ctx.log
    if not log:
        return

    if _is_topic_switch(user_input):
        to_archive = list(log)
        brief = _compactor.get_summary() or _make_brief_summary(to_archive)
        new_log: list[dict] = []
        if brief:
            new_log.append({
                "role": "system",
                "content": f"⏪ 前一话题已结束。{brief}",
            })
        log[:] = new_log
        logger.info("话题切换检测到，已压缩 %d 条旧对话", len(to_archive))
        return

    retain_budget = int(MAX_CONTEXT_TOKENS * _COMPACT_RETAIN_RATIO)
    accumulated = 0
    retain_start = len(log)

    for i in range(len(log) - 1, -1, -1):
        content = log[i].get("content", "") or ""
        tokens = len(content) * 0.35
        if accumulated + tokens > retain_budget:
            break
        accumulated += tokens
        retain_start = i

    while retain_start < len(log) and log[retain_start].get("role") != "user":
        retain_start += 1

    if retain_start <= 0:
        return

    to_archive = log[:retain_start]
    brief = _compactor.get_summary() or _make_brief_summary(to_archive)

    new_log = []
    if brief:
        new_log.append({
            "role": "system",
            "content": f"⏪ 对话摘要：{brief}",
        })
    new_log.extend(log[retain_start:])
    log[:] = new_log
    logger.info("上下文压缩完成：保留 %d/%d 条消息", len(log), len(log) + len(to_archive))


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
# 后台压缩器 — 持续生成日志摘要，压缩时瞬时完成
# 模仿 Claude Code 的 Session Memory 后台持续更新机制
# ═══════════════════════════════════════════════════════════════

_BG_CHECK_INTERVAL = 20       # 后台检查间隔（秒）
_BG_MIN_NEW_MSGS = 8          # 每次触发需要的最小新消息数
_BG_RETAIN_TAIL = 12          # 保留最近 N 条不归档（留给正在进行的对话）
_BG_MAX_SUMMARY_LEN = 1000    # 摘要总长度上限

# LLM 摘要系统提示词（固定 → DeepSeek 前缀缓存永久命中，0 增量 token）
_SUMMARY_SYSTEM_PROMPT = (
    "You are a session memory updater for a coding assistant.\n"
    "Given previous summary and new conversation messages, "
    "produce an updated 1-2 sentence summary.\n"
    "Focus on: actions taken, code changes, key decisions, current status.\n"
    "Be precise and specific. If nothing meaningful changed, say so briefly."
)


def _make_llm_summary(prev_summary: str | None, messages: list[dict]) -> str:
    """LLM semantic summary using Flash model. Fixed system prompt -> prefix cache hit."""
    # Same-module references, no circular import risk

    parts: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            parts.append(f"user: {content[:200]}")
        elif role == "assistant":
            text = content[:200]
            if text:
                parts.append(f"asst: {text}")
        elif role == "tool":
            first_line = content.split("\n")[0][:80]
            tid = m.get("tool_call_id", "")
            if first_line:
                parts.append(f"tool({tid[:8]}): {first_line}")

    if not parts:
        return ""

    msgs_text = "\n".join(parts)

    api_msgs = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Previous summary: {prev_summary or '(none)'}\n\n"
            f"New messages:\n{msgs_text}\n\n"
            f"Updated summary (1-2 sentences):"
        )},
    ]

    try:
        backend = auto_select_model("summarize")
        result, _ = _invoke_llm(backend, api_msgs, lambda _: None)
        summary = (result or "").strip()
        if summary:
            return summary[:_BG_MAX_SUMMARY_LEN]
    except Exception:
        logger.warning("LLM summary failed, fallback to brief", exc_info=True)

    return ""


class BackgroundCompactor:
    """Background context compactor with daemon thread for instant compaction."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._summary: str | None = None
        self._running = False
        self._ctx: CacheContext | None = None
        self._last_idx: int = 0

    def get_summary(self) -> str | None:
        with self._lock:
            return self._summary

    def start(self, ctx: CacheContext) -> None:
        self._ctx = ctx
        self._summary = None
        self._last_idx = 0
        if self._thread is None or not self._thread.is_alive():
            self._running = True
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="bg-compactor"
            )
            self._thread.start()
            logger.info("bg-compactor started")

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("bg-compactor error")
            time.sleep(_BG_CHECK_INTERVAL)

    def _tick(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        log = ctx.log
        if not log:
            self._last_idx = 0
            with self._lock:
                self._summary = None
            return

        if len(log) < self._last_idx:
            self._last_idx = 0
            with self._lock:
                self._summary = None
            return

        if len(log) < self._last_idx + _BG_MIN_NEW_MSGS:
            return

        end = len(log) - _BG_RETAIN_TAIL
        if end <= self._last_idx:
            self._last_idx = max(0, len(log) - _BG_RETAIN_TAIL)
            return

        to_summarize = log[self._last_idx:end]
        self._last_idx = end

        # LLM 语义摘要（system prompt 固定 → 缓存命中），失败则 fallback
        brief = _make_llm_summary(self._summary, to_summarize)
        if not brief:
            brief = _make_brief_summary(to_summarize, max_len=200)
            if not brief:
                return
            # fallback 时仍需追加尾部
            with self._lock:
                if self._summary:
                    self._summary = (self._summary + " | " + brief)[:_BG_MAX_SUMMARY_LEN]
                else:
                    self._summary = brief
        else:
            with self._lock:
                self._summary = brief

        logger.debug("bg-summary updated via LLM: +%d msgs", len(to_summarize))
# 模块级单例（run_conversation 中按需启动）
_compactor = BackgroundCompactor()


# ═══════════════════════════════════════════════════════════════
# _build_api_messages 已由 CacheContext.send() 替代
# 保留一个兼容包装以便渐进迁移
# ═══════════════════════════════════════════════════════════════

def _build_api_messages(messages: list[dict]) -> list[dict]:
    """向后兼容：从扁平 messages 构建 API 消息。

    ⚠️ 已废弃：新代码请使用 CacheContext.send()。
    """
    from .cache_context import CacheContext
    # 临时构建 CacheContext 来复用 send() 逻辑
    prefix = [m for m in messages if m.get("role") == "system"]
    log = [m for m in messages if m.get("role") != "system"]
    tmp = CacheContext(prefix_msgs=prefix, log_msgs=log)
    return tmp.send(validate=False)



# ═══════════════════════════════════════════════════════════════
# SAFE — 工具并行分发
# ═══════════════════════════════════════════════════════════════

# 并行安全白名单：纯读取工具，无副作用，可在同批次内同时执行
_PARALLEL_SAFE: frozenset[str] = frozenset({
    # 文件读取
    "read_file", "read_file_lines", "list_files", "count_lines",
    # 代码搜索
    "grep_code",
    # 网络查询
    "search_web", "read_page",
    # Git 读取（状态/日志/差异/分支）
    "git_status", "git_diff", "git_log", "git_branch",
    # RAG 查询
    "rag_query", "rag_status",
    # 记忆查询
    "recall",
    # 项目扫描/检查
    "scan_project", "check_project", "docs_sync_check", "generate_agents_md",
    # 插件/能力
    "discover", "plugin_spec", "list_plugins",
    # MCP 列表
})

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


def _compute_prefix_hash(messages: list[dict]) -> tuple[str, int, int]:
    """Calculate system message prefix hash. Delegates to cache_context module."""
    from .cache_context import compute_prefix_hash
    return compute_prefix_hash(messages)


def _execute_tool_batch(approved: list[tuple]) -> list[str]:
    """执行一批已批准的工具调用，并行分发只读工具。

    Args:
        approved: [(tc_data, tool_name, args, tc), ...] 已批准按原始顺序

    Returns:
        list[str] 与 approved 一一对应的执行结果
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = len(approved)
    results: list[str] = [""] * n

    # 先处理有预置结果（跳过）的工具
    for i in range(n):
        pre = approved[i][4]  # pre_result
        if pre is not None:
            results[i] = pre

    # 剩余需要实际执行的，拆分为并行组和串行组
    parallel_idxs = [i for i in range(n) if approved[i][4] is None and approved[i][1] in _PARALLEL_SAFE]
    serial_idxs = [i for i in range(n) if approved[i][4] is None and approved[i][1] not in _PARALLEL_SAFE]

    # ── 并行执行只读工具 ──
    if parallel_idxs:
        names = [approved[i][1] for i in parallel_idxs]
        print(f"  ⚡ [SAFE] 并行执行 {len(parallel_idxs)} 个工具: {', '.join(names)}")
        with ThreadPoolExecutor(max_workers=min(len(parallel_idxs), 8)) as pool:
            fut_map: dict = {}
            for i in parallel_idxs:
                tc = approved[i][3]  # (tc_data, tool_name, args, tc, pre_result)[3]
                fut = pool.submit(execute_tool, tc)
                fut_map[fut] = i
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    results[idx] = json.dumps({"error": f"并行执行失败: {e}"}, ensure_ascii=False)

    # ── 串行执行有副作用的工具 ──
    for i in serial_idxs:
        tc = approved[i][3]  # (tc_data, tool_name, args, tc, pre_result)[3]
        try:
            results[i] = execute_tool(tc)
        except Exception as e:
            results[i] = json.dumps({"error": f"执行失败: {e}"}, ensure_ascii=False)

    _update_safe_stats(len(parallel_idxs), len(serial_idxs))
    return results
def run_conversation(
    ctx: CacheContext,
    user_input: str,
    on_token: TokenCallback,
    on_tool: ToolCallback,
    on_progress: Callable[[], None] | None = None,
    session_id: str = "",
) -> str:
    """处理一轮对话：自动路由 + 工具调用循环。

    Args:
        ctx: CacheContext 实例（三段式上下文管理器）
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
    # 追加用户输入到 log（prefix 不变）
    ctx.log.append({"role": "user", "content": user_input})

    # 重设 Storm 去重窗口 + SAFE 并行统计（同轮工具循环内）
    from .tools.storm import reset_storm
    reset_storm()
    reset_safe_stats()

    # 自动选择模型
    backend = auto_select_model(user_input)
    model_name = backend.info.name
    logger.info("路由: '%s...' → %s", user_input[:30], model_name)

    # 如果选的不是当前模型，通知用户
    if backend is not _current_backend:
        on_token(f"\n[🤖 使用 {model_name} 处理此任务]\n\n")

    # 确保后台压缩器已启动（幂等）
    _compactor.start(ctx)

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
            logger.info("触发自动压缩：%d >= %d (70%%)", used, compact_limit)
            _compact_context(ctx, user_input)
            logger.info("压缩后消息数: %d", len(ctx))

        try:
            content, tool_calls = _invoke_llm(backend, ctx.send(), on_token, session_id)
        except UserInterrupt:
            clear_interrupt()
            return "\n\n[⏹️ 已中断]"
        if tool_calls:
            ctx.log.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            # ═══════════════════════════════════════════════════════════
            # SAFE Phase 1：安全确认（顺序执行，含用户交互）
            # ═══════════════════════════════════════════════════════════
            from .safety import check_tool, get_mode, get_safety_level, trust_tool

            approved: list[tuple] = []  # (tc_data, tool_name, args, tc, pre_result_or_None)

            for tc_data in tool_calls:
                tc = SimpleNamespace(
                    function=SimpleNamespace(
                        name=tc_data["function"]["name"],
                        arguments=tc_data["function"]["arguments"],
                    )
                )
                args = json.loads(tc_data["function"]["arguments"])
                tool_name = tc_data["function"]["name"]
                on_tool(tool_name, args)

                safety = check_tool(tool_name)
                if safety == "confirm":
                    level = get_safety_level(tool_name).value
                    mode = get_mode()
                    label = _TOOL_LABEL_MAP.get(tool_name, tool_name)
                    detail = " ".join(f"{k}={v}" for k, v in args.items())
                    if len(detail) > 120:
                        detail = detail[:117] + "..."

                    print(f"\n  ⚠️ [{label}] 需要确认")
                    print(f"     等级: {level.upper()} | 模式: {mode}")
                    print(f"     参数: {detail}")
                    choice = input("     执行？[y=确认/n=跳过/a=始终允许本次会话] ").strip().lower()

                    if choice in ("a", "always"):
                        trust_tool(tool_name)
                        print(f"     ✅ 已信任 {tool_name}，本次会话自动放行\n")
                        approved.append((tc_data, tool_name, args, tc, None))
                    elif choice in ("y", "yes", ""):
                        approved.append((tc_data, tool_name, args, tc, None))
                    else:
                        print("     ⛔ 已跳过\n")
                        approved.append((tc_data, tool_name, args, tc,
                                          f"⛔ [{tool_name}] 已跳过（用户未批准）"))
                else:
                    approved.append((tc_data, tool_name, args, tc, None))

            # ═══════════════════════════════════════════════════════════
            # SAFE Phase 2：批量执行（只读并行 + 有副作用串行）
            # ═══════════════════════════════════════════════════════════
            exec_indices: list[int] = []
            exec_items: list[tuple] = []
            for i, item in enumerate(approved):
                if item[4] is None:  # pre_result is None → 需要执行
                    exec_indices.append(i)
                    exec_items.append(item)

            if exec_items:
                exec_results = _execute_tool_batch(exec_items)
                for idx, result in zip(exec_indices, exec_results):
                    tc_data, tool_name, args, tc, _ = approved[idx]
                    approved[idx] = (tc_data, tool_name, args, tc, result)

            # ═══════════════════════════════════════════════════════════
            # SAFE Phase 3：处理后追加到 ctx.log
            # ═══════════════════════════════════════════════════════════
            for tc_data, tool_name, args, tc, result in approved:
                result = truncate_to_budget(result, ctx.all)
                result = _compress_tool_result(tool_name, result)
                ctx.log.append({
                    "role": "tool",
                    "tool_call_id": tc_data["id"],
                    "content": result,
                })
            if on_progress:
                on_progress()
        else:
            ctx.log.append({"role": "assistant", "content": content or ""})
            if on_progress:
                on_progress()
            return content or ""