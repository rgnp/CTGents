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
    compact_keep_ratio: float = _env_float("CTG_COMPACT_KEEP_RATIO", 0.50)


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
class RuntimeParams:
    """运行时旋钮：LLM 重试、代码执行、token 预算/估算。"""

    # eager 工具执行线程池大小（LLM 流式期间预启动 SAFE 工具）
    eager_executor_workers: int = _env_int("CTG_EAGER_EXECUTOR_WORKERS", 8)
    max_retries: int = _env_int("CTG_MAX_RETRIES", 3)
    # 重试退避基数（秒），实际延迟 = base * 2**(attempt-1)
    retry_base_delay: float = _env_float("CTG_RETRY_BASE_DELAY", 1.0)
    # run_python 代码执行超时（秒）
    max_exec_timeout: int = _env_int("CTG_MAX_EXEC_TIMEOUT", 5)
    # 单轮工具循环最大 API 请求数（成本熔断：失控循环唯一的钱闸）
    max_requests_per_turn: int = _env_int("CTG_MAX_REQUESTS_PER_TURN", 60)
    # 一轮请求数达此值却没有 current.md 任务 → 提示 agent 建任务（事实触发，判断留 agent）。
    task_suggest_min_requests: int = _env_int("CTG_TASK_SUGGEST_MIN_REQUESTS", 6)
    # 长任务自主续跑：一次用户回合内最多自主驱动多少步（不用反复手动"继续"）。真正的
    # 失控守卫是 task_stall_limit（连续没推进就停）+ 每步 max_requests_per_turn 熔断；
    # 本值只是"跑多久回来跟用户对一次"的生成性上限，故给得较宽（超长任务少打断）。
    task_continue_budget: int = _env_int("CTG_TASK_CONTINUE_BUDGET", 30)
    # 长任务续跑卡死保险：连续多少步没推进 current.md 就停下交还用户（容一次忘勾标记的
    # 宽限，不像旧版一步没改就停；停的正式信号是 agent 调 need_user/task_done，这只是兜底）。
    task_stall_limit: int = _env_int("CTG_TASK_STALL_LIMIT", 2)
    # 单条工具结果允许占用的上下文比例上限
    tool_result_budget: float = _env_float("CTG_TOOL_RESULT_BUDGET", 0.15)
    # 工具结果超过此字符数即压缩（read_file 等除外，见 SKIP_COMPRESS_TOOLS）
    tool_result_compress_threshold: int = _env_int("CTG_TOOL_RESULT_COMPRESS_THRESHOLD", 2400)
    # read_file 无行号参数（整文件读）的字符上限。超限只返回前段+行数提示，逼按需切片。
    # read_file 在 SKIP_COMPRESS_TOOLS 里不被下游压缩，必须在源头截，否则整文件灌进对话=
    # 单次巨型 miss + 把 payload 推向 65% 压缩核弹（llm.py 72k 字符≈2.5万 token，比整轮对话还大）。
    # 带 start_line/end_line 的切片读不受此限（agent 明确要某段）。
    read_file_max_chars: int = _env_int("CTG_READ_FILE_MAX_CHARS", 24000)
    # token 估算（无 tokenizer 的粗估，分字符类）：中文每字 / 其他每字符。
    # 可用 API 返回的 prompt_tokens 真值对账校准这两个旋钮。
    token_per_char_cjk: float = _env_float("CTG_TOKEN_PER_CHAR_CJK", 0.6)
    token_per_char_other: float = _env_float("CTG_TOKEN_PER_CHAR_OTHER", 0.3)
    # git commit 超时地板（秒）：质量门全量 pytest 需 ~40s+，timeout 给小了
    # 正道必死，会把 agent 推向绕门——commit 命令的 timeout 自动抬到此值
    git_commit_timeout_floor: int = _env_int("CTG_GIT_COMMIT_TIMEOUT_FLOOR", 300)
    # 测试命令（pytest）超时地板（秒）：快速套件 ~40s、全量 ~150s 都 > run_command
    # 默认 30s/run_async 默认 120s → 跑测试必超时被杀，逼 agent 去 async+反复 poll。
    # 同步 pytest 命令的 timeout 自动抬到此值，让它一轮跑完拿结果，不超时不轮询。
    test_timeout_floor: int = _env_int("CTG_TEST_TIMEOUT_FLOOR", 240)
    # poll 长轮询等待预算（秒）：poll 内部阻塞等到作业完成或等够此值才返回。取消 poll
    # 去重后，poll 立即返回"运行中"会让 agent ~1s 一次忙等长任务——每次 poll = 一整个
    # LLM 往返、重发上下文烧前缀缓存。内部阻塞把上百次往返塌成十来次；不超过作业剩余超时。
    poll_wait_seconds: int = _env_int("CTG_POLL_WAIT_SECONDS", 15)


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
    # 低于此分的记忆不返回(>1 = 滤掉只撞单个 body token 的灰尘命中;
    # 跨库索引 archive 后噪音变多,抬离 0。注:地板治不了高分词汇撞库,那是 token 重叠固有限制)
    recall_min_score: float = _env_float("CTG_MEMORY_RECALL_MIN_SCORE", 1.0)
    # 命中字段权重:name > description > body
    weight_name: float = _env_float("CTG_MEMORY_WEIGHT_NAME", 3.0)
    weight_desc: float = _env_float("CTG_MEMORY_WEIGHT_DESC", 2.0)
    weight_body: float = _env_float("CTG_MEMORY_WEIGHT_BODY", 1.0)
    # 完整查询作为子串命中的强力加成(保留精确短语优先)
    exact_bonus: float = _env_float("CTG_MEMORY_EXACT_BONUS", 5.0)
    # 时间衰减率：每天旧记忆分数乘 1/(1+age_days*decay_rate)。0=关，0.01≈百天减半
    decay_rate: float = _env_float("CTG_MEMORY_DECAY_RATE", 0.0)


MEMORY = MemoryParams()

@dataclass(frozen=True)
class UserModelParams:
    """用户理解档案收割旋钮（收割 prompt 等结构性细节留 user_model.py）。"""

    # 总开关：会话结束是否做"懂你"收割（关掉则零额外 LLM 调用）
    enabled: bool = _env_bool("CTG_USER_MODEL_ENABLED", True)
    # 少于这么多条用户消息的会话不收割（避免给琐碎会话花一次调用）
    min_user_messages: int = _env_int("CTG_USER_MODEL_MIN_USER_MSGS", 3)
    # 档案正文目标长度（字）——prompt 软约束，超出由清洗硬截
    max_profile_chars: int = _env_int("CTG_USER_MODEL_MAX_PROFILE_CHARS", 1500)
    # 喂给收割调用的对话精华预算（字符，取尾部近因）
    digest_char_budget: int = _env_int("CTG_USER_MODEL_DIGEST_BUDGET", 6000)
    # 收割调用网络失败重试次数
    harvest_retries: int = _env_int("CTG_USER_MODEL_HARVEST_RETRIES", 2)


USER_MODEL = UserModelParams()


@dataclass(frozen=True)
class ProjectKnowledgeParams:
    """项目/领域知识收割旋钮（收割 prompt 等结构性细节留 project_model.py）。

    与 UserModelParams 对称：user_model 收"懂你这个人"，本组收"懂你这摊活"——
    durable 的项目/领域事实（数据集/约定/造过的工具/领域门道/项目约束）。
    type=knowledge → 摘要注入 + recall 取详情（不全文注入，防长知识烧前缀缓存）。
    """

    enabled: bool = _env_bool("CTG_PROJECT_KNOWLEDGE_ENABLED", True)
    min_user_messages: int = _env_int("CTG_PROJECT_KNOWLEDGE_MIN_USER_MSGS", 3)
    max_body_chars: int = _env_int("CTG_PROJECT_KNOWLEDGE_MAX_BODY_CHARS", 1800)
    digest_char_budget: int = _env_int("CTG_PROJECT_KNOWLEDGE_DIGEST_BUDGET", 7000)
    harvest_retries: int = _env_int("CTG_PROJECT_KNOWLEDGE_HARVEST_RETRIES", 2)


PROJECT_KNOWLEDGE = ProjectKnowledgeParams()
