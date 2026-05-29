"""配置中心：环境变量 → 安全配置 + 模型配置 + 行为参数。"""

import os
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv
from tavily import TavilyClient

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
# Flash: 快速/便宜/带工具调用，适合日常问答和简单操作
MODEL_FLASH: str = os.getenv("MODEL_FLASH", "deepseek-v4-flash")
FLASH_MAX_TOKENS: int = int(os.getenv("FLASH_MAX_TOKENS", "8192"))

# Pro: 强推理/代码生成，适合复杂任务
MODEL_PRO: str = os.getenv("MODEL_PRO", "deepseek-v4-pro")
PRO_MAX_TOKENS: int = int(os.getenv("PRO_MAX_TOKENS", "8192"))

# Tavily 搜索
TAVILY_API_KEY: str = _require_env("TAVILY_API_KEY")

# ── 行为参数 ──
TOOL_LOOP_THRESHOLD: float = 0.85
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 1.0
MAX_EXEC_TIMEOUT: int = 5

# ── 路径 ──
SESSION_DIR: str = str(Path(__file__).parent.parent / "sessions")
PLUGINS_DIR: str = str(Path(__file__).parent.parent / "plugins")
MEMORY_DIR: str = str(Path(__file__).parent.parent / "memory")

# ── Token 预算 ──
MAX_CONTEXT_TOKENS: int = 960_000
TOOL_RESULT_BUDGET: float = 0.3
TOKEN_PER_CHAR: float = 0.5

# 客户端（模块级单例，惰性初始化）
_tavily_client: TavilyClient | None = None


def get_tavily_client() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    return _tavily_client
