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


def _env_str(key: str, default: str) -> str:
    raw = os.getenv(key)
    return raw if raw is not None else default


@dataclass(frozen=True)
class ContextParams:
    """上下文窗口与压缩/清理相关旋钮。"""

    # 上下文 token 上限（达到 tool_loop_threshold 比例即拒绝继续）。
    # 设 50w 而非模型 96w 物理上限：实测最多忍受 ~30w 实际上下文就觉得注意力散了，
    # 50w×65%=32.5w 触发压缩，正落在该区间。判定量的是"实际发出去"的体积（见
    # _live_context_tokens：中段折叠后），不是 self.log 原文全长。
    max_context_tokens: int = _env_int("CTG_MAX_CONTEXT_TOKENS", 500_000)
    # 工具循环硬顶：用量达此比例即停止本轮，提示开新会话
    tool_loop_threshold: float = _env_float("CTG_TOOL_LOOP_THRESHOLD", 0.95)
    # 有损 compaction 触发线已改用绝对舒适区上界 comfort_zone_high（见下），旧的
    # compact_threshold 比例旋钮已删——它当时是 0.65×MAX 的悬崖，现在被舒适区取代。
    # 压缩后保留最近多少比例的消息
    compact_keep_ratio: float = _env_float("CTG_COMPACT_KEEP_RATIO", 0.50)
    # 防抖重新武装：连续低效压缩停掉后，用量再涨 MAX 的此比例 → 解防抖再试一次。
    # 治"单向锁"——清零原本只在有效压缩里发生，被跳过后永不复位 = 自动压缩永久关闭。
    compact_rearm_growth: float = _env_float("CTG_COMPACT_REARM_GROWTH", 0.10)

    # ── 中段陈旧工具结果折叠（send() 视图变换，不动 self.log）──
    # 文字稿洁净优先于缓存命中：把不在最近 N 轮内的大工具结果折成一行 stub，
    # 让 LLM 注意力别被陈旧 read/命令输出稀释。原文留在 self.log（落盘 + 可 recall）。
    stale_tool_collapse_enabled: bool = _env_bool("CTG_STALE_TOOL_COLLAPSE", True)
    # 最近多少个 user 轮内的工具结果逐字保留（热区，刚读的文件还要用）
    stale_tool_keep_turns: int = _env_int("CTG_STALE_TOOL_KEEP_TURNS", 3)
    # 工具结果超过多少字符才折叠（小结果折了没意义、还丢信号）
    stale_tool_collapse_threshold: int = _env_int("CTG_STALE_TOOL_COLLAPSE_THRESHOLD", 600)

    # ── 舒适区自适应折叠：让 live 长期稳定在 [comfort_low, comfort_high]（绝对 token，
    # 对齐心智「15-25w」、与 MAX 解耦）。离上限越近折得越狠，都仍无损可 fetch。──
    # 折叠量按 pre-fold 体积估（与 stats 同口径 //4，自含、不调 _live_context_tokens 防递归）。
    # < comfort_zone_low：不折，全保真（在舒适区下方、有空间，零 fetch 摩擦）。
    comfort_zone_low: int = _env_int("CTG_COMFORT_ZONE_LOW", 150_000)
    # ≥ comfort_zone_high：紧逼档，狠折把 live 拉回舒适区（spike 读了很多后 1-2 轮归位）。
    comfort_zone_high: int = _env_int("CTG_COMFORT_ZONE_HIGH", 250_000)
    # 紧逼档：更少轮内保留（更早折）+ 更小阈值（小结果也折）。
    stale_tool_squeeze_keep_turns: int = _env_int("CTG_STALE_TOOL_SQUEEZE_KEEP_TURNS", 1)
    stale_tool_squeeze_threshold: int = _env_int("CTG_STALE_TOOL_SQUEEZE_THRESHOLD", 300)


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
    # 索引落盘目录（cwd 相对）。rag.py 的词面索引与 embeddings.py 的向量索引共用此目录——
    # 单一真相源，两处曾各自硬编码 ".rag-index"，改一处会静默分裂到两个位置。
    index_dir: str = _env_str("CTG_RAG_INDEX_DIR", ".rag-index")
    # 本地 embedding 语义检索（knowledge/ 研究文档索引专用，代码索引不受影响）
    embed_enabled: bool = _env_bool("CTG_RAG_EMBED_ENABLED", True)
    embed_model: str = _env_str("CTG_RAG_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
    embed_max_chars: int = _env_int("CTG_RAG_EMBED_MAX_CHARS", 2000)
    lexical_weight: float = _env_float("CTG_RAG_LEXICAL_WEIGHT", 0.4)  # 向量权重 = 1 - 此值


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
    max_requests_per_turn: int = _env_int("CTG_MAX_REQUESTS_PER_TURN", 180)
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
    # git commit 超时地板（秒）：提交门现在只跑 ruff（~2s），此地板已是冗余余量
    # （历史上为"门内全量 pytest"留的，pytest 已不进提交门）；留着无害，给慢环境兜底。
    git_commit_timeout_floor: int = _env_int("CTG_GIT_COMMIT_TIMEOUT_FLOOR", 300)
    # 测试命令（pytest）超时地板（秒）：快速套件 ~40s、全量 ~150s 都 > run_command
    # 默认 30s/run_async 默认 120s → 跑测试必超时被杀，逼 agent 去 async+反复 poll。
    # 同步 pytest 命令的 timeout 自动抬到此值，让它一轮跑完拿结果，不超时不轮询。
    test_timeout_floor: int = _env_int("CTG_TEST_TIMEOUT_FLOOR", 240)
    # poll 长轮询等待预算（秒）：poll 内部阻塞等到作业完成或等够此值才返回。取消 poll
    # 去重后，poll 立即返回"运行中"会让 agent ~1s 一次忙等长任务——每次 poll = 一整个
    # LLM 往返、重发上下文烧前缀缓存。内部阻塞把上百次往返塌成十来次；不超过作业剩余超时。
    poll_wait_seconds: int = _env_int("CTG_POLL_WAIT_SECONDS", 15)
    # 后台作业 TTL（秒）：超时后自动杀进程回收。默认 3600（1 小时），CTG_JOB_TTL_SECONDS 覆盖。
    # 原来 600s 对一些长任务（pip install / 模型推理）太短。
    job_ttl_seconds: int = _env_int("CTG_JOB_TTL_SECONDS", 3600)


RUNTIME = RuntimeParams()


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


# UserModelParams / ProjectKnowledgeParams 已随会话结束自动 harvest 整体删除
# （2026-06-23）：记忆改为靠 agent 显式 remember 生长，不再 LLM 收割用户/项目档案。


@dataclass(frozen=True)
class TaskParams:
    """长任务规划旋钮（planning_interval 等结构性参数，见 tasks.py / task_loop.py）。"""

    # 自主续跑时每 N 步做一次计划审查（agent 停下来评估：计划还匹配现实吗？要不要重规划？）
    # 0 = 关（旧行为，一直做不做审查）。建议值：3~5。
    planning_interval: int = _env_int("CTG_PLANNING_INTERVAL", 3)
    # 计划审查的预算步数：一次审查最多用多少步来调计划（防止 agent 在"调计划"里螺旋出不
    # 去）。包含审查本身和后续的 update_plan 等工具调用。
    plan_review_budget: int = _env_int("CTG_PLAN_REVIEW_BUDGET", 5)


TASK = TaskParams()


@dataclass(frozen=True)
class SummaryParams:
    """会话摘要（跨会话记忆的生产侧）+ 前缀情景索引旋钮。"""

    # 会话结束用 LLM(Flash) 生成语义摘要（话题/脉络/未竟事项），失败自动退回规则提取。
    # 与被删的「LLM 收割」不同：这是 append-only 导航索引，不重写既有记忆断言。
    use_llm: bool = _env_bool("CTG_SUMMARY_LLM", True)
    # 喂给摘要 LLM 的对话文字稿字符上限（超出保头尾、中间标记省略）
    digest_max_chars: int = _env_int("CTG_SUMMARY_DIGEST_MAX_CHARS", 12000)
    # 前缀会话索引：最多列最近多少场
    index_sessions: int = _env_int("CTG_SUMMARY_INDEX_SESSIONS", 25)
    # 前缀会话索引：最近多少场附带未竟事项（"接着做"的钩子，全带太占前缀）
    index_unfinished: int = _env_int("CTG_SUMMARY_INDEX_UNFINISHED", 8)


SUMMARY = SummaryParams()


@dataclass(frozen=True)
class DelegateParams:
    """delegate 调研子代理旋钮（worker 隔离上下文 + 机械出处闸，见 tools/delegate.py）。"""

    enabled: bool = _env_bool("CTG_DELEGATE_ENABLED", True)
    # worker 单次任务的 API 请求预算（钱闸：不继承主轮 180 的全局熔断）
    worker_max_requests: int = _env_int("CTG_DELEGATE_WORKER_MAX_REQUESTS", 40)
    # 出处闸打回后，每轮重试的请求预算（补 read_page/重写产出，用不了多少步）
    retry_max_requests: int = _env_int("CTG_DELEGATE_RETRY_MAX_REQUESTS", 15)
    # 出处闸不过时最多打回 worker 重试几次（之后 fail-closed 标记未通过返回）
    gate_retries: int = _env_int("CTG_DELEGATE_GATE_RETRIES", 1)
    # 产出文件最少字符数（低于视为未交付）
    min_output_chars: int = _env_int("CTG_DELEGATE_MIN_OUTPUT_CHARS", 200)
    # worker 可用工具子集（逗号分隔工具名；不含 delegate 自身防递归，不含 control 工具）
    worker_tools: str = os.getenv(
        "CTG_DELEGATE_WORKER_TOOLS",
        "search_web,read_page,read_file,list_files,write_file,rag_search,think",
    )


DELEGATE = DelegateParams()
