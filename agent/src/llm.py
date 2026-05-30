"""LLM 后端抽象：多模型支持、自动路由、流式调用。"""

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
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
from .tools import count_messages_tokens, execute_tool, get_tools, truncate_to_budget

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
        msg = response.choices[0].message

        tool_calls: list[dict] | None = None
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
    for key, backend in AVAILABLE_MODELS.items():
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


# ═══════════════════════════════════════════════════════════════
# 对话循环（核心）
# ═══════════════════════════════════════════════════════════════


def _invoke_llm(
    backend: LLMBackend,
    messages: list[dict],
    on_token: TokenCallback,
) -> tuple[str | None, list[dict] | None]:
    """调用 LLM，带重试机制。首次流式，失败后降级为非流式重试。"""
    tools = get_tools() if backend.info.supports_tools else None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if attempt == 1:
                return backend.chat_stream(messages, on_token, tools)
            else:
                logger.warning("流式失败，降级为非流式重试 (%d/%d)...", attempt, MAX_RETRIES)
                return backend.chat_non_stream(messages, on_token, tools)
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
_TOOL_RESULT_COMPRESS_THRESHOLD = 3000

# 不压缩的工具（这些工具的结果通常短小或结构重要）
_TOOL_RESULT_NO_COMPRESS = {
    "git_status", "git_diff", "git_log", "git_branch",
    "check_project", "docs_sync_check", "count_lines",
}


def _compress_tool_result(tool_name: str, result: str) -> str:
    """压缩过大的工具结果，减少后续轮次的 token 消耗。

    策略：
    - 小于阈值 → 不压缩
    - 大于阈值 → 保留前 N 字符 + 截断提示
    - 特定工具（read_file 等）→ 额外提示可重新读取
    """
    if len(result) <= _TOOL_RESULT_COMPRESS_THRESHOLD:
        return result
    if tool_name in _TOOL_RESULT_NO_COMPRESS:
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

def _build_api_messages(messages: list[dict]) -> list[dict]:
    """构建发给 API 的消息列表。

    过滤 _volatile 消息（运行时上下文，仅首次需要，后续轮次不需要重复发送）。
    非系统消息保持追加顺序，保障前缀缓存稳定性。
    """
    api: list[dict] = []
    for m in messages:
        if m.get("role") == "system" and not m.get("_volatile"):
            api.append(m)
    for m in messages:
        if m.get("role") != "system":
            api.append(m)
    return api


def run_conversation(
    messages: list[dict],
    user_input: str,
    on_token: TokenCallback,
    on_tool: ToolCallback,
    on_progress: Callable[[], None] | None = None,
) -> str:
    """处理一轮对话：自动路由 + 工具调用循环。"""
    copy: list[dict] = list(messages)
    copy.append({"role": "user", "content": user_input})
    messages[:] = copy

    # 自动选择模型
    backend = auto_select_model(user_input)
    model_name = backend.info.name
    logger.info("路由: '%s...' → %s", user_input[:30], model_name)

    # 如果选的不是当前模型，通知用户
    if backend is not _current_backend:
        on_token(f"\n[🤖 使用 {model_name} 处理此任务]\n\n")

    while True:
        used = count_messages_tokens(copy)
        limit = int(MAX_CONTEXT_TOKENS * TOOL_LOOP_THRESHOLD)
        if used >= limit:
            return (
                f"上下文用量已达上限（{used}/{MAX_CONTEXT_TOKENS} tokens）。"
                "请开启新会话或精简问题。"
            )

        try:
            content, tool_calls = _invoke_llm(backend, _build_api_messages(copy), on_token)
        except UserInterrupt:
            clear_interrupt()
            return "\n\n[⏹️ 已中断]"

        if tool_calls:
            copy.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
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

                # ═══════════════════════════════════════════════
                # Auto Mode 安全检查
                # ═══════════════════════════════════════════════
                from .safety import check_tool, trust_tool, get_mode, get_safety_level

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
                        result = execute_tool(tc)
                    elif choice in ("y", "yes", ""):
                        result = execute_tool(tc)
                    else:
                        result = f"⛔ [{tool_name}] 已跳过（用户未批准）"
                        print("     ⛔ 已跳过\n")
                else:
                    result = execute_tool(tc)

                result = truncate_to_budget(result, copy)
                # Phase 2: 压缩过大的工具结果
                result = _compress_tool_result(tool_name, result)
                copy.append({
                    "role": "tool",
                    "tool_call_id": tc_data["id"],
                    "content": result,
                })
            messages[:] = copy
            if on_progress:
                on_progress()
        else:
            copy.append({"role": "assistant", "content": content or ""})
            messages[:] = copy
            if on_progress:
                on_progress()
            return content or ""
