"""自我认知系统 — 结构化架构知识 + 动态运行时数据。

设计原则：
- 静态知识（子系统职责、连接关系、设计理由）→ 手写维护，随代码演进更新
- 动态数据（工具数、覆盖率、模型状态）→ 每次调用实时采集
- 合并输出 → agent 拿到的是"既有骨架又有血肉"的完整自画像
"""

import os
from pathlib import Path

TOOLS_SELF = [
    {
        "_meta": {"label": "自我认知"},
        "type": "function",
        "function": {
            "name": "self",
            "description": "查看自己的能力与架构：子系统/连接/运行时状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["full", "capabilities", "architecture", "connections", "runtime", "params"],
                        "description": "full=全部/capabilities/architecture/connections/runtime/params(可调旋钮)",
                    },
                },
                "required": [],
            },
        },
    },
]


def execute(name: str, args: dict) -> str | None:
    if name == "self":
        scope = args.get("scope", "full")
        return build_self_portrait(scope)
    return None


# ═══════════════════════════════════════════════════════════════
# 架构知识库（静态 — 手写维护，描述设计意图和连接关系）
# ═══════════════════════════════════════════════════════════════

SYSTEM_MAP = {
    "llm": {
        "name": "LLM 对话系统",
        "files": "src/llm.py",
        "what": "模型调用、流式输出、工具批处理、前缀缓存命中追踪。始终使用 Pro 模型",
        "why": "DeepSeek 前缀缓存按字节匹配，工具定义序列化一致保障缓存命中；固定单一 Pro 模型，无切换以稳住前缀",
        "tools": [],
        "connections": {
            "config": "读取 API key、模型 ID、超时参数",
            "cache_context": "三段式上下文（prefix/log/scratch）传给 LLM",
            "tools/__init__": "获取工具列表序列化给 API，执行工具调用",
            "main": "被主循环调用 run_conversation()",
        },
    },
    "tools": {
        "name": "工具系统",
        "files": "src/tools/__init__.py + src/tools/*.py",
        "what": "工具的注册、调度、执行、热加载。Storm 去重，模块化注册（_BUILTIN_MODULES 清单）",
        "why": "热加载无需重启，_BUILTIN_MODULES 是唯一真相源——所有工具由此注册，任何别处硬编码的工具名都可能漂移",
        "tools": ["self"],
        "connections": {
            "storm": "同轮重复调用被去重，返回缓存结果",
            "rag": "写入文件后增量更新代码索引",
        },
    },
    "cache_context": {
        "name": "上下文缓存系统",
        "files": "src/cache_context.py",
        "what": (
            "三段式 CacheContext：不可变 prefix（享受缓存）+ 追加 log（对话历史）"
            "+ 临时 scratch（不缓存的动态内容）"
        ),
        "why": "DeepSeek 前缀缓存按字节匹配——prefix 不变则全命中，log 增量部分仅首次计费。_volatile 标记控制持久化",
        "tools": [],
        "connections": {
            "llm": "ctx.send() 产出 API 消息列表",
            "main": "主循环维护 ctx.log，/clear 时重建 prefix",
            "commands": "命令向 ctx.log 注入系统消息",
        },
    },
    "commands": {
        "name": "指令系统",
        "files": "src/commands.py",
        "what": "/help /evolve /model /self 等终端命令",
        "why": "@builtin 装饰器注册模式，CmdResult 控制后续行为（retry/save/clear）",
        "tools": [],
        "connections": {
            "evolution_runner": "/evolve 创建 run/state/patch，并注入本轮运行契约",
            "llm": "/model 切换模型，/clear 重置",
            "self": "/self 调用 build_self_portrait()",
            "evolve": "/stats 读取进化统计",
        },
    },
    "guard": {
        "name": "自我保护系统",
        "files": "src/guard.py + src/coverage_gate.py",
        "what": "is_protected() 保护关键文件不被修改 + 覆盖率门禁渐进解锁",
        "why": "agent 可修改自身代码，约束是靠 is_protected() 硬保护 guard.py + coverage_gate 覆盖率门槛控制",
        "tools": [],
        "connections": {
            "tools/file": "write_file/edit_file_lines 前检查 is_protected()",
        },
    },
    "evolution": {
        "name": "进化系统",
        "files": "src/evolution_runner.py + src/evolve.py + src/validate.py",
        "what": "runner 管理 run/state/patch，研究→综合→生成→验证→合入/修复 闭环。JSONL 档案支持相似搜索",
        "why": "agent 的自修改需要可追踪运行态，而不是只靠一次性 prompt；runner 记录基线、验证和收口状态",
        "tools": [
            "evolve_query",
            "evolve_status",
            "evolve_check_access",
            "evolve_coverage",
            "evolve_suggest_tests",
            "evolve_validate",
        ],
        "connections": {
            "validate": "三阶段验证（AST→pytest→覆盖率/lint）",
            "coverage_gate": "改文件前检查权限，覆盖率不足时建议测试",
            "git": "git_commit 使用具体文件暂存，提交前强制 ruff + pytest",
            "evolution_runner": "/evolve 启动 active run，evolve_validate/git_commit 回写状态",
            "evolve": "每次尝试写入 JSONL 进化档案",
            "llm": "委托 LLM 执行代码修改",
        },
    },
    "memory": {
        "name": "记忆系统",
        "files": "src/tools/memory.py + memory/（项目级目录，frontmatter .md + MEMORY.md 索引）",
        "what": (
            "remember/recall/forget + 写信号探测。存储为带 frontmatter 的 .md 文件，"
            "MEMORY.md 是名称+摘要索引；recall 按子串匹配命中文件、返回片段（非语义检索）；"
            "remember/forget 后重建索引。detect_signal 机械探测「该记」的时机，写入仍由 agent 自愿"
        ),
        "why": "agent 需要跨会话记住用户偏好和重要事实；索引进前缀随时可见，详情按需 recall",
        "tools": ["remember", "recall", "forget"],
        "connections": {
            "main": "启动时 get_context() 注入索引；新 user 消息经 detect_signal 挂写信号",
            "llm": "压缩时若记忆变脏（is_dirty）则刷新注入的索引",
        },
    },
    "rag": {
        "name": "RAG 语义搜索",
        "files": "src/tools/rag.py",
        "what": (
            "TF-IDF 索引——代码（src/*.py）和知识库（knowledge/*.md）双库独立。"
            "rag_index 索引代码，rag_index_research 索引研究笔记，"
            "rag_query/rag_search 分别搜索"
        ),
        "why": "两套独立索引：代码和知识库数据量差异大，分开搜索更精准",
        "tools": ["rag_index", "rag_query", "rag_status", "rag_index_research", "rag_search"],
        "connections": {
            "tools/__init__": "写入文件后增量更新代码索引",
            "knowledge": "knowledge/ 目录中的研究笔记通过 rag_index_research 建立索引",
        },
    },
}

# 子系统的连接关系图（用于 architecture scope）
CONNECTION_GRAPH = [
    ("main", "llm", "用户输入 → run_conversation() → LLM 回复"),
    ("main", "commands", "以 / 开头的输入 → dispatch() → CmdResult"),
    ("llm", "cache_context", "ctx.send() 序列化消息 → API 调用"),
    ("llm", "tools/__init__", "get_tools() → API tool definitions"),
    ("tools/__init__", "storm", "同轮重复调用 → 返回缓存结果"),
    ("evolution", "validate", "改完后跑三阶段验证"),
    ("evolution", "coverage_gate", "改前检查 can_modify()"),
    ("evolution", "evolve", "结果记录到 JSONL 档案"),
    ("evolution", "git", "成功提交后关闭 active runner"),
    ("commands", "evolution_runner", "/evolve → run/state/patch + 进化 prompt"),
    ("commands", "self", "/self → build_self_portrait()"),
]


def build_self_portrait(scope: str = "full") -> str:
    """合并静态架构知识 + 动态运行时数据。"""
    lines = ["# CTGents 自我认知", ""]

    if scope in ("full", "capabilities"):
        lines.append(_capabilities_section())

    if scope in ("full", "architecture"):
        lines.append(_architecture_section())

    if scope in ("full", "connections"):
        lines.append(_connections_section())

    if scope in ("full", "runtime"):
        lines.append(_runtime_section())

    if scope in ("full", "params"):
        lines.append(_params_section())

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 功能全景
# ═══════════════════════════════════════════════════════════════

def _capabilities_section() -> str:
    lines = [
        "## 功能全景",
        "",
        f"我由 {len(SYSTEM_MAP)} 个子系统组成，每个有自己的职责、工具、和联动关系。",
        "",
    ]
    for key, sysinfo in SYSTEM_MAP.items():
        lines.append(f"### {sysinfo['name']}（{key}）")
        lines.append(f"文件: {sysinfo['files']}")
        lines.append(f"做什么: {sysinfo['what']}")
        lines.append(f"为什么这样设计: {sysinfo['why']}")
        if sysinfo["tools"]:
            lines.append(f"工具: {', '.join(sysinfo['tools'])}")
        lines.append("联动:")
        for dep, desc in sysinfo["connections"].items():
            lines.append(f"  → {dep} — {desc}")
        lines.append("")

    # 动态补充：按子系统统计工具分布
    try:
        from . import get_tools
        tools = get_tools()
        lines.append("---")
        lines.append(f"当前运行时: {len(tools)} 个工具已注册")
    except Exception:
        pass

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 架构设计
# ═══════════════════════════════════════════════════════════════

def _architecture_section() -> str:
    lines = [
        "## 架构设计",
        "",
        "### 设计哲学",
        "1. 缓存优先 — DeepSeek 前缀缓存按字节匹配，前缀不变则全命中。",
        "   因此前缀保持稳定，固定单一 Pro 模型（无 flash/无切换），工具定义序列化一致。",
        "2. 渐进披露 — 不是所有信息都塞上下文。自省用 self 工具，搜索用 RAG。",
        "3. 测试门禁 — agent 理论上能改任何代码，靠 is_protected() 硬保护 guard.py + 覆盖率门槛控制。",
        "4. 自洽 — 写完代码自动更新 RAG 索引，不需要人工记得。",
        "",
        "### 为什么是这个结构",
        "main.py 是外壳（I/O 循环 + 启动初始化），llm.py 是大脑（模型调用 + 工具批处理），",
        "tools/ 是手（工具操作文件/网络/代码/git），cache_context.py 是记忆（三段式上下文），",
        "guard.py 是免疫系统（is_protected 保护关键文件）。"
        "\n"
        "进化/记忆/RAG 是上层能力——它们通过工具暴露给 LLM，LLM 自主决定何时调用。",
        "",
        "### 数据流",
        "```",
        "用户输入 → main.py",
        "  ├─ / 开头 → commands.py → CmdResult（retry/save/clear）",
        "  └─ 其他 → llm.run_conversation()",
        "              ├─ ctx.send() 序列化 → API 调用",
        "              ├─ tool_calls → execute_tool() → 工具执行 → 结果回注 ctx.log",
        "              └─ 循环直到无 tool_calls 或超限",
        "```",
        "",
        "### 启动流程",
        "1. 加载环境配置 → 构建 CacheContext prefix",
        "2. prefix 包含 AGENTS.md（行为约束）+ 项目上下文",
        "3. 追加 volatile 系统消息（记忆/RAG/反思）到 log",
        "4. 进入 read–eval–print 循环",
        "",
        "### 当前 src/ 目录树",
    ]

    src_dir = Path(__file__).resolve().parent.parent
    tree_lines = _render_tree(src_dir, prefix="  ", root=str(src_dir))
    lines.append("```")
    lines.extend(tree_lines)
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 连接关系
# ═══════════════════════════════════════════════════════════════

def _connections_section() -> str:
    lines = [
        "## 子系统联动",
        "",
        f"共 {len(CONNECTION_GRAPH)} 条关键连接关系：",
        "",
    ]
    for src, dst, desc in CONNECTION_GRAPH:
        lines.append(f"  {src} ──→ {dst}  {desc}")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 运行时（动态）
# ═══════════════════════════════════════════════════════════════

def _runtime_section() -> str:
    lines = ["## 运行时状态", ""]

    # 模型
    try:
        from ..llm import get_current_model_name
        lines.append(f"模型: {get_current_model_name()}")
    except Exception as e:
        lines.append(f"模型: 获取失败 ({e})")

    # 工具
    try:
        from . import get_tools
        tools = get_tools()
        lines.append(f"工具: {len(tools)} 个已注册 | PID: {os.getpid()}")
    except Exception:
        pass

    # 覆盖率
    try:
        from ..coverage_gate import get_overall_coverage
        cov = get_overall_coverage()
        lines.append(f"覆盖率: {cov:.0%}")
    except Exception:
        pass

    # RAG
    try:
        from .rag import get_index_status
        rag = get_index_status()
        indexed = "已索引" if "未建立" not in rag else "未索引"
        lines.append(f"RAG: {indexed}")
    except Exception:
        pass

    # 进化
    try:
        from ..evolve import get_stats
        stats = get_stats()
        lines.append(f"进化: {stats.get('total', 0)} 条记录 | 成功率 {stats.get('success_rate', 0):.0%}")
    except Exception:
        pass

    # 记忆
    try:
        from .memory import get_context as mem_ctx
        mem = mem_ctx()
        lines.append(f"记忆: {'有记忆' if mem else '空'}")
    except Exception:
        pass

    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 可调旋钮（params.py 各域当前值 + env 覆盖情况）
# ═══════════════════════════════════════════════════════════════

def _params_section() -> str:
    from dataclasses import fields

    from .. import params

    domains = [
        ("上下文 CONTEXT", params.CONTEXT),
        ("RAG", params.RAG),
        ("进化 EVOLUTION", params.EVOLUTION),
        ("运行时 RUNTIME", params.RUNTIME),
    ]
    lines = ["## 可调旋钮（params.py）", ""]
    for title, obj in domains:
        lines.append(f"### {title}")
        for f in fields(obj):
            lines.append(f"  {f.name} = {getattr(obj, f.name)}")
        lines.append("")

    overrides = sorted(
        k for k in os.environ
        if k.startswith("CTG_") or k == "EVOLVE_REQUIRE_CLEAN"
    )
    lines.append(f"env 覆盖中: {', '.join(overrides) if overrides else '无（全部默认）'}")
    lines.append("用 CTG_<NAME> 环境变量可覆盖任意旋钮（改完需重启生效）。")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════

def _render_tree(path: Path, prefix: str, root: str) -> list[str]:
    result: list[str] = []
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return result
    # 先过滤再 enumerate：否则被跳过的尾项会让最后一个可见项错配 ├──（应为 └──）
    visible = [
        e for e in entries
        if not e.name.startswith("__pycache__")
        and not (e.name.startswith(".") and e.name != ".gitkeep")
    ]
    for i, entry in enumerate(visible):
        is_last = i == len(visible) - 1
        connector = "└── " if is_last else "├── "
        result.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            ext = "    " if is_last else "│   "
            result.extend(_render_tree(entry, prefix + ext, root))
    return result
