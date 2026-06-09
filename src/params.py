"""集中可调旋钮（behavior knobs），按域分组的 frozen dataclass。

只收"会被人/agent 拧来改行为"的参数：阈值、比例、超时、权重、开关。
结构性局部细节（文件名、正则、扩展名表、格式串）留在各自模块——
搬进来只会制造耦合、把这里变成 god-config，反噬模块化。

每个旋钮可用 `CTG_<NAME>` 环境变量覆盖（.env 或 shell）。
缓存无影响：这些是代码常量，不进 DeepSeek API prompt 前缀。

新增一域：加一个 `@dataclass(frozen=True)` + 一个模块级单例实例即可，
各模块从这里绑定本地名（`X = DOMAIN.knob`），保持读起来自然。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 与 config 同源、幂等：保证读 env 前 .env 已加载（无论谁先 import）。
load_dotenv(Path(__file__).parent.parent / ".env")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    return float(raw) if raw is not None else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw is not None else default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    return raw == "1" if raw is not None else default


@dataclass(frozen=True)
class ContextParams:
    """上下文窗口与压缩/清理相关旋钮。"""

    # 上下文 token 上限（达到 tool_loop_threshold 比例即拒绝继续）
    max_context_tokens: int = _env_int("CTG_MAX_CONTEXT_TOKENS", 960_000)
    # 工具循环硬顶：用量达此比例即停止本轮，提示开新会话
    tool_loop_threshold: float = _env_float("CTG_TOOL_LOOP_THRESHOLD", 0.95)
    # 滑窗压缩触发比例：超过即驱逐旧对话换摘要
    compact_threshold: float = _env_float("CTG_COMPACT_THRESHOLD", 0.65)
    # 压缩后保留最近多少比例的消息
    compact_keep_ratio: float = _env_float("CTG_COMPACT_KEEP_RATIO", 0.40)


CONTEXT = ContextParams()


@dataclass(frozen=True)
class RagParams:
    """RAG 索引/检索旋钮（结构性的文件名、正则、忽略表留在 rag.py）。"""

    # 分块
    max_chunk_lines: int = _env_int("CTG_RAG_MAX_CHUNK_LINES", 50)
    min_chunk_lines: int = _env_int("CTG_RAG_MIN_CHUNK_LINES", 10)
    max_chunk_chars: int = _env_int("CTG_RAG_MAX_CHUNK_CHARS", 2000)
    # 检索
    default_top_k: int = _env_int("CTG_RAG_TOP_K", 5)
    search_min_score: float = _env_float("CTG_RAG_MIN_SCORE", 0.05)
    # 关键词权重
    weight_name: float = _env_float("CTG_RAG_WEIGHT_NAME", 3.0)
    weight_comment: float = _env_float("CTG_RAG_WEIGHT_COMMENT", 2.0)
    weight_code: float = _env_float("CTG_RAG_WEIGHT_CODE", 1.0)
    weight_identifier: float = _env_float("CTG_RAG_WEIGHT_IDENTIFIER", 1.5)
    # 超过此大小（字节）的文件跳过索引
    max_file_size: int = _env_int("CTG_RAG_MAX_FILE_SIZE", 512 * 1024)


RAG = RagParams()


@dataclass(frozen=True)
class EvolutionParams:
    """自进化 runner 旋钮（run 目录/状态文件名等结构性细节留在 evolution_runner.py）。"""

    # git 子命令超时（秒）
    git_timeout_seconds: int = _env_int("CTG_EVOLVE_GIT_TIMEOUT", 10)
    # 写入 run 事件的 git status 预览最大字符数
    prompt_status_limit: int = _env_int("CTG_EVOLVE_STATUS_LIMIT", 1600)
    # 干净基线闸：开启后脏树拒绝启动进化（默认关）
    require_clean: bool = _env_bool("EVOLVE_REQUIRE_CLEAN", False)


EVOLUTION = EvolutionParams()


@dataclass(frozen=True)
class RuntimeParams:
    """运行时旋钮：LLM 重试、代码执行、token 预算/估算。"""

    # LLM 调用最大重试次数
    max_retries: int = _env_int("CTG_MAX_RETRIES", 3)
    # 重试退避基数（秒），实际延迟 = base * 2**(attempt-1)
    retry_base_delay: float = _env_float("CTG_RETRY_BASE_DELAY", 1.0)
    # run_python 代码执行超时（秒）
    max_exec_timeout: int = _env_int("CTG_MAX_EXEC_TIMEOUT", 5)
    # 单条工具结果允许占用的上下文比例上限
    tool_result_budget: float = _env_float("CTG_TOOL_RESULT_BUDGET", 0.15)
    # 工具结果超过此字符数即压缩（read_file 等除外，见 SKIP_COMPRESS_TOOLS）
    tool_result_compress_threshold: int = _env_int("CTG_TOOL_RESULT_COMPRESS_THRESHOLD", 1200)
    # token 估算：每字符约多少 token（无 tokenizer 时的粗估）
    token_per_char: float = _env_float("CTG_TOKEN_PER_CHAR", 0.5)


RUNTIME = RuntimeParams()


@dataclass(frozen=True)
class PinboardParams:
    """会话钉板旋钮(structural 的标记串/渲染格式留在 session_pins.py)。"""

    # 钉板最多容纳几条(超出踢最旧整条;小到能"一眼扫完",不复制中段衰减)
    max_items: int = _env_int("CTG_PINBOARD_MAX_ITEMS", 8)
    # 每条 pin 最多几字(逼原子化;超出写入侧截断,绝不切某条中间)
    max_chars: int = _env_int("CTG_PINBOARD_MAX_CHARS", 80)


PINBOARD = PinboardParams()


@dataclass(frozen=True)
class MemoryParams:
    """记忆 recall 排序检索旋钮(结构性的正则/片段长度留在 memory.py)。"""

    # 排序后最多返回几条
    recall_top_k: int = _env_int("CTG_MEMORY_RECALL_TOP_K", 5)
    # 低于此分的记忆不返回(默认 0 = 只要命中任一 token 就算)
    recall_min_score: float = _env_float("CTG_MEMORY_RECALL_MIN_SCORE", 0.0)
    # 命中字段权重:name > description > body
    weight_name: float = _env_float("CTG_MEMORY_WEIGHT_NAME", 3.0)
    weight_desc: float = _env_float("CTG_MEMORY_WEIGHT_DESC", 2.0)
    weight_body: float = _env_float("CTG_MEMORY_WEIGHT_BODY", 1.0)
    # 完整查询作为子串命中的强力加成(保留精确短语优先)
    exact_bonus: float = _env_float("CTG_MEMORY_EXACT_BONUS", 5.0)


MEMORY = MemoryParams()
