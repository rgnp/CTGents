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
# 工具调用最大轮数。瘦身搜索后每轮约需 2 次调用（搜→读），
# 科研调研至少3-4个维度，设 10 给足余量。
MAX_TOOL_ROUNDS: int = 10
# LLM API 最大重试次数
MAX_RETRIES: int = 3
# 重试退避基础间隔（秒）：1s → 2s → 4s
RETRY_BASE_DELAY: float = 1.0
# 代码执行超时（秒），防止死循环或恶意代码占用资源
MAX_EXEC_TIMEOUT: int = 5

# ── 路径 ──
SESSION_DIR: str = str(Path(__file__).parent.parent / "sessions")

# ── Token 预算（核心设计）──
# 放弃了写死字符数的做法，改为动态 token 预算管理。
# 每次工具调用前计算当前 messages 已占 token，按剩余空间的一定比例分配给工具结果。
# 比例可调：设太大会挤占对话历史空间，设太小会丢失工具结果信息。
MAX_CONTEXT_TOKENS: int = 960_000   # 总上下文上限（DeepSeek V4 1M，留 4 万给输出）
TOOL_RESULT_BUDGET: float = 0.3    # 工具结果最多占用当前剩余 token 的 30%
# 保守估算系数：中英文混合文本约 0.4 token/字符，取 0.5 宁可多估不漏估
TOKEN_PER_CHAR: float = 0.5

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
