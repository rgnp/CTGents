"""配置中心：密钥/模型/路径。可调行为旋钮见 params.py（按域分组）。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from tavily import TavilyClient

from .params import CONTEXT, EVOLUTION, RUNTIME

load_dotenv(Path(__file__).parent.parent / ".env")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"缺少环境变量: {key}（请在 .env 中配置）")
    return value


# ── API 密钥 ──
DEEPSEEK_API_KEY: str = _require_env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ── 双模型配置 ──
# Flash: 快速/省钱（1元/M入, 2元/M出），32K 输出足够日常操作和问答
MODEL_FLASH: str = os.getenv("MODEL_FLASH", "deepseek-v4-flash")
FLASH_MAX_TOKENS: int = int(os.getenv("FLASH_MAX_TOKENS", "32768"))

# Pro: 强推理/长代码（3元/M入, 6元/M出），64K 给大型重构留空间
MODEL_PRO: str = os.getenv("MODEL_PRO", "deepseek-v4-pro")
PRO_MAX_TOKENS: int = int(os.getenv("PRO_MAX_TOKENS", "65536"))

# Tavily 搜索
TAVILY_API_KEY: str = _require_env("TAVILY_API_KEY")

# ── 行为旋钮（真值在 params.py 按域分组；此处仅绑定本地名保持 import 兼容）──
TOOL_LOOP_THRESHOLD: float = CONTEXT.tool_loop_threshold
MAX_CONTEXT_TOKENS: int = CONTEXT.max_context_tokens
EVOLVE_REQUIRE_CLEAN: bool = EVOLUTION.require_clean  # 仍由 EVOLVE_REQUIRE_CLEAN env 覆盖
MAX_RETRIES: int = RUNTIME.max_retries
RETRY_BASE_DELAY: float = RUNTIME.retry_base_delay
MAX_EXEC_TIMEOUT: int = RUNTIME.max_exec_timeout
TOOL_RESULT_BUDGET: float = RUNTIME.tool_result_budget
TOKEN_PER_CHAR: float = RUNTIME.token_per_char

# ── 路径 ──
SESSION_DIR: str = str(Path(__file__).parent.parent / "sessions")
MEMORY_DIR: str = str(Path(__file__).parent.parent / "memory")

# 客户端（模块级单例，惰性初始化）
_tavily_client: TavilyClient | None = None


def get_tavily_client() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    return _tavily_client
