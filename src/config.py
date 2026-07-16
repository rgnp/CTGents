"""配置中心：密钥/模型/路径。可调行为旋钮见 params.py（按域分组）。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from tavily import InvalidAPIKeyError, TavilyClient, UsageLimitExceededError
from tavily.errors import ForbiddenError

from .params import CONTEXT, RUNTIME

load_dotenv(Path(__file__).parent.parent / ".env")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"缺少环境变量: {key}（请在 .env 中配置）")
    return value


# -- API 密钥 --
DEEPSEEK_API_KEY: str = _require_env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# -- 模型配置（始终 Pro：固定单模型才能养肥 DeepSeek 前缀缓存）--
# Pro: 强推理/长代码（3元/M入, 6元/M出），64K 给大型重构留空间
MODEL_PRO: str = os.getenv("MODEL_PRO", "deepseek-v4-pro")
PRO_MAX_TOKENS: int = int(os.getenv("PRO_MAX_TOKENS", "65536"))
# Flash: 同 provider 的快/省档（deepseek-v4-flash）。切到它会换 KV 缓存命名空间、
# 前缀缓存另起一份——缓存已判定为服务端问题、不再是约束，故按需可切。
MODEL_FLASH: str = os.getenv("MODEL_FLASH", "deepseek-v4-flash")
FLASH_MAX_TOKENS: int = int(os.getenv("FLASH_MAX_TOKENS", "8192"))

# Tavily 搜索（多 key 轮换：逗号分隔，遇额度耗尽自动切下一个）
_TAVILY_KEYS_RAW: str = os.getenv("TAVILY_API_KEYS", os.getenv("TAVILY_API_KEY", ""))
TAVILY_API_KEYS: list[str] = [k.strip() for k in _TAVILY_KEYS_RAW.split(",") if k.strip()]
if not TAVILY_API_KEYS:
    raise RuntimeError(
        "缺少环境变量: TAVILY_API_KEYS（请在 .env 中配置，逗号分隔多个 key）"
    )

# Semantic Scholar（可选）：scan_conf 查顶会论文用；缺省为空，工具会降级提示
S2_API_KEY: str = os.getenv("S2_API_KEY", "")

# -- 行为旋钮（真值在 params.py 按域分组；此处仅绑定本地名保持 import 兼容）--
TOOL_LOOP_THRESHOLD: float = CONTEXT.tool_loop_threshold
MAX_CONTEXT_TOKENS: int = CONTEXT.max_context_tokens
MAX_RETRIES: int = RUNTIME.max_retries
RETRY_BASE_DELAY: float = RUNTIME.retry_base_delay
LLM_TIMEOUT: float = RUNTIME.llm_timeout
MAX_EXEC_TIMEOUT: int = RUNTIME.max_exec_timeout
TOOL_RESULT_BUDGET: float = RUNTIME.tool_result_budget
TOKEN_PER_CHAR_CJK: float = RUNTIME.token_per_char_cjk
TOKEN_PER_CHAR_OTHER: float = RUNTIME.token_per_char_other

# -- 路径 --
SESSION_DIR: str = str(Path(__file__).parent.parent / "sessions")
MEMORY_DIR: str = str(Path(__file__).parent.parent / "memory")
# 任务归档:架构教训多写在这里。recall 也索引它，否则它对检索是"只写不读的坟场"。
ARCHIVE_DIR: str = str(Path(__file__).parent.parent / "tasks" / "archive")


class MultiKeyTavilyClient:
    """TavilyClient wrapper: multi API key auto-rotation.

    Automatically rotates to next key on UsageLimitExceededError,
    ForbiddenError, or InvalidAPIKeyError. Raises after all keys exhausted.
    Other methods transparently delegate to current TavilyClient.
    """

    def __init__(self, api_keys: list[str]) -> None:
        if not api_keys:
            raise ValueError("api_keys 不能为空")
        self._api_keys = api_keys
        self._idx = 0
        self._clients: dict[int, TavilyClient] = {}

    @property
    def _client(self) -> TavilyClient:
        if self._idx not in self._clients:
            self._clients[self._idx] = TavilyClient(api_key=self._api_keys[self._idx])
        return self._clients[self._idx]

    def _rotate(self) -> bool:
        """Switch to next key. Returns True on success, False if no more keys."""
        if self._idx + 1 >= len(self._api_keys):
            return False
        self._idx += 1
        return True

    def _promote_current_key(self) -> None:
        """Persist current working key as first in .env TAVILY_API_KEYS.

        Ensures cross-session / self-heal rebuilds start from a known-good key
        instead of hitting already-exhausted key 0 every time.
        """
        if self._idx == 0:
            return
        promoted_key = self._api_keys[self._idx]
        new_keys = [promoted_key] + [k for i, k in enumerate(self._api_keys) if i != self._idx]
        self._api_keys = new_keys
        self._idx = 0
        self._clients = {}
        # Write back to .env file
        env_path = Path(__file__).resolve().parent.parent / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines: list[str] = []
        for line in lines:
            if line.startswith("TAVILY_API_KEYS="):
                new_lines.append(f"TAVILY_API_KEYS={','.join(new_keys)}\n")
            else:
                new_lines.append(line)
        env_path.write_text("".join(new_lines), encoding="utf-8")
        # Sync os.environ so same-process load_dotenv(override=True) picks it up
        os.environ["TAVILY_API_KEYS"] = ",".join(new_keys)

    def search(self, *args: object, **kwargs: object) -> dict:
        rotated = False
        for _ in range(len(self._api_keys)):
            try:
                result = self._client.search(*args, **kwargs)
                if rotated:
                    self._promote_current_key()
                return result
            except (UsageLimitExceededError, ForbiddenError, InvalidAPIKeyError):
                if not self._rotate():
                    raise
                rotated = True
        raise UsageLimitExceededError("所有 Tavily API key 均已耗尽")

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


# 客户端（模块级单例，惰性初始化）
_tavily_client: MultiKeyTavilyClient | None = None


def get_tavily_client() -> MultiKeyTavilyClient:
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = MultiKeyTavilyClient(TAVILY_API_KEYS)
    return _tavily_client
