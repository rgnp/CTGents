import os
from openai import OpenAI
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()


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

# 常量
MAX_TOOL_ROUNDS: int = 5
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 1.0  # 秒，指数退避：1s → 2s → 4s
SESSION_DIR: str = os.path.join(os.path.dirname(__file__), "sessions")

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
