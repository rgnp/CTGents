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


# LLM
DEEPSEEK_API_KEY: str = _require_env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Tavily
TAVILY_API_KEY: str = _require_env("TAVILY_API_KEY")

# ── 行为参数 ──
# 工具调用最大的轮数，防止agent陷入无尽的搜索-阅读循环
MAX_TOOL_ROUNDS: int = 5
# LLM API 最大重试次数
MAX_RETRIES: int = 3
# 重试退避基础间隔（秒）：1s → 2s → 4s
RETRY_BASE_DELAY: float = 1.0

# ── 路径 ──
SESSION_DIR: str = str(Path(__file__).parent.parent / "sessions")

# ── 内容长度上限 ──
# 这些值没有固定标准，取决于模型上下文窗口和任务需求。
# 设太大会稀释 LLM 注意力（前面的内容被忽略），设太小会丢信息。
# 当前基于 DeepSeek V4 1M token（约 70 万中文字符）设定，
# 保留大量余量给对话本身，可随时按实际效果调整。
MAX_PAGE_CHARS: int = 8000
MAX_FILE_CHARS: int = 10000

# 客户端（模块级单例，惰性初始化）
_llm_client: OpenAI | None = None
_tavily_client: TavilyClient | None = None


def get_llm_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _llm_client


def get_tavily_client() -> TavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
    return _tavily_client
