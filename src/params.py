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
    # 工具结果清理触发比例（贴近压缩点，过早会每轮断前缀缓存）
    cleanup_threshold: float = _env_float("CTG_CLEANUP_THRESHOLD", 0.60)
    # 一轮内工具结果达此数量才值得清理（太少不值得断缓存）
    cleanup_min_tool_results: int = _env_int("CTG_CLEANUP_MIN_TOOL_RESULTS", 2)


CONTEXT = ContextParams()
