"""自我认知系统 — 结构化架构知识 + 动态运行时数据。

设计原则：
- 静态知识（子系统职责、连接关系、设计理由）→ 手写维护，随代码演进更新
- 动态数据（工具数、覆盖率、模型状态）→ 每次调用实时采集
- 合并输出 → agent 拿到的是"既有骨架又有血肉"的完整自画像
"""

import os
import sys
import time
from pathlib import Path

TOOLS_SELF = [
    {
        "type": "function",
        "function": {
            "name": "self",
            "description": (
                "查看自己的完整能力与架构。返回结构化肖像：有什么子系统、各自做什么、"
                "之间如何联动、当前运行时状态。用户问'你能做什么''你怎么设计的'时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["full", "capabilities", "architecture", "connections", "runtime"],
                        "description": (
                            "full=全部, capabilities=功能清单(工具+子系统+联动), "
                            "architecture=架构设计理由, connections=子系统间联动关系, runtime=当前状态"
                        ),
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
        "what": "模型调用、流式输出、工具批处理、前缀缓存命中追踪",
        "why": "粘性模型（一个会话一个模型）避免 DeepSeek 前缀缓存碎片化；默认 Pro 负责决策，Flash 用于子代理探索",
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
        "what": "58 个工具的注册、调度、执行、热加载。插件优先执行，Storm 去重，写入后自动维护一致性",
        "why": "模块化工具注册（_BUILTIN_MODULES 清单），热加载无需重启，一致性钩子保证写文件后 RAG/覆盖率/插件自动刷新",
        "tools": ["discover", "self"],
        "connections": {
            "plugin_mgr": "插件工具与内置工具合并到同一注册表",
            "storm": "同轮重复调用被去重，返回缓存结果",
            "tracker": "每次工具调用记录耗时和成败",
            "reflect": "失败调用触发反思记录",
            "coverage_gate": "写入 src/*.py 后清除覆盖率缓存",
            "rag": "写入文件后增量更新代码索引",
            "mcp": "MCP 工具动态合并到工具列表",
        },
    },
    "cache_context": {
        "name": "上下文缓存系统",
        "files": "src/cache_context.py",
        "what": "三段式 CacheContext：不可变 prefix（享受缓存）+ 追加 log（对话历史）+ 临时 scratch（不缓存的动态内容）",
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
        "what": "/help /evolve /research /model /self /health /stats 等终端命令",
        "why": "@builtin 装饰器注册模式，CmdResult 控制后续行为（retry/save/clear）",
        "tools": [],
        "connections": {
            "evolution_loop": "/evolve 和 /research 注入进化 prompt",
            "llm": "/model 切换粘性模型，/clear 重置",
            "self": "/self 和 /health 调用 build_self_portrait()",
            "evolve": "/stats 读取进化统计",
        },
    },
    "guard": {
        "name": "自我保护系统",
        "files": "src/guard.py + src/coverage_gate.py",
        "what": "两层保护——崩溃自愈（traceback 分析→回滚→重试）+ 函数级关联测试门禁（tier 渐进解锁）",
        "why": "agent 可修改自身代码，约束是函数级关联测试：要改的函数必须有测试覆盖。guard.py 永不被修改",
        "tools": [],
        "connections": {
            "coverage_gate": "is_protected() 委托 can_modify() 判断",
            "tools/file": "write_file 前检查 is_protected()",
            "main": "崩溃时外层 try-catch 调用 analyze_crash→execute_rollback",
        },
    },
    "evolution": {
        "name": "进化系统",
        "files": "src/evolution_loop.py + src/evolve.py + src/validate.py",
        "what": "研究→综合→生成→验证→合入/回滚 闭环。JSONL 进化档案支持 TF-IDF 相似搜索和学习",
        "why": "agent 能在网上研究更好的设计、生成候选方案、落地代码、跑测试验证、失败自动回滚、记录教训",
        "tools": ["evolve_query", "evolve_status", "evolve_check_access", "evolve_coverage", "evolve_suggest_tests", "evolve_validate"],
        "connections": {
            "validate": "三阶段验证（AST→pytest→覆盖率/lint）",
            "coverage_gate": "改文件前检查权限，覆盖率不足时建议测试",
            "git": "改前 git_commit 快照，失败 git reset --hard",
            "evolve": "每次尝试写入 JSONL 进化档案",
            "llm": "委托 LLM 执行代码修改",
        },
    },
    "research": {
        "name": "研究知识库",
        "files": "src/tools/research.py + knowledge/",
        "what": "论文搜索（arXiv+Semantic Scholar）、笔记保存、知识库查询。SQLite 存储 + RAG 语义索引",
        "why": "agent 需要学习外部知识来改进自己——不只是搜网页，还要持久化、可检索",
        "tools": ["search_papers", "read_paper", "save_note", "search_knowledge", "kb_topics", "link_papers", "kb_stats"],
        "connections": {
            "rag": "写入论文/笔记后自动索引到 RAG 研究库",
            "web": "search_web/read_page 用于补充在线资料",
        },
    },
    "memory": {
        "name": "记忆系统",
        "files": "src/tools/memory.py + ~/.claude/projects/.../memory/",
        "what": "remember/recall/forget。混合检索——RAG 语义搜索优先，关键词回退。时间衰减评分（相似度×0.6+新近度×0.3+重要性×0.1）",
        "why": "agent 需要跨会话记住用户偏好和重要事实。不是存了就完——会衰减、会浮现、会关联",
        "tools": ["remember", "recall", "forget"],
        "connections": {
            "rag": "写入/删除记忆后自动重建 RAG 记忆索引",
            "main": "启动时 get_context() 注入上下文",
        },
    },
    "rag": {
        "name": "RAG 语义搜索",
        "files": "src/tools/rag.py",
        "what": "TF-IDF 索引——代码（src/*.py）、研究（knowledge/）、记忆（memory/）三库独立。渐进披露：browse→query→read",
        "why": "大规模代码库不能全塞上下文。按需检索——先看概览（browse），再精确搜（query），最后读原文（read）",
        "tools": ["rag_query", "rag_status", "rag_browse", "rag_read", "rag_index", "rag_index_research"],
        "connections": {
            "tools/__init__": "写入文件后增量更新代码索引",
            "research": "论文/笔记写入后更新研究索引",
            "memory": "记忆变更后更新记忆索引",
        },
    },
    "safety": {
        "name": "安全系统",
        "files": "src/safety.py",
        "what": "MANUAL/SAFE/AUTO 三级安全模式。危险操作（删文件/强推/执行命令）需确认。白名单机制",
        "why": "agent 能改代码但不应随意删文件或 force push",
        "tools": [],
        "connections": {
            "llm": "run_conversation 中每轮工具调用前过安全检查",
            "guard": "受保护文件列表参考 is_protected()",
        },
    },
}

# 子系统的连接关系图（用于 architecture scope）
CONNECTION_GRAPH = [
    ("main", "llm", "用户输入 → run_conversation() → LLM 回复"),
    ("main", "commands", "以 / 开头的输入 → dispatch() → CmdResult"),
    
    ("main", "guard", "崩溃时 analyze_crash() → execute_rollback() → 重试"),
    ("llm", "cache_context", "ctx.send() 序列化消息 → API 调用"),
    ("llm", "tools/__init__", "get_tools() → API tool definitions"),
    ("tools/__init__", "coverage_gate", "写入 src/*.py → clear_cache()"),
    ("tools/__init__", "plugin_mgr", "写入 plugins/*.py → reload_plugins()"),
    ("tools/__init__", "storm", "同轮重复调用 → 返回缓存结果"),
    ("guard", "coverage_gate", "is_protected() → can_modify()"),
    ("guard", "tools/file", "write_file 前调用 is_protected()"),
    ("evolution", "validate", "改完后跑三阶段验证"),
    ("evolution", "coverage_gate", "改前检查 can_modify()"),
    ("evolution", "evolve", "结果记录到 JSONL 档案"),
    ("evolution", "git", "改前 commit 快照，失败 reset"),
    ("research", "rag", "论文/笔记 → SQLite → RAG 索引"),
    ("memory", "rag", "记忆变更 → RAG 索引更新"),
    ("commands", "evolution_loop", "/evolve /research → 进化 prompt"),
    ("commands", "self", "/self /health → build_self_portrait()"),
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

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 功能全景
# ═══════════════════════════════════════════════════════════════

def _capabilities_section() -> str:
    lines = [
        "## 功能全景",
        "",
        "我由 13 个子系统组成，每个有自己的职责、工具、和联动关系。",
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
        lines.append(f"---")
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
        "   因此 system prompt 极简（24 token），粘性模型避免切换，工具定义序列化一致。",
        "2. 渐进披露 — 不是所有信息都塞上下文。自省用 self 工具，搜索用 RAG，研究用子代理。",
        "3. 无文件锁 — agent 理论上能改任何代码，靠函数级关联测试门禁约束：修改前须有测试保护。",
        "4. 自洽 — 写完代码自动刷新 RAG/覆盖率/插件，不需要人工记得。",
        "",
        "### 为什么是这个结构",
        "main.py 是外壳（I/O 循环 + 启动初始化），llm.py 是大脑（模型调用 + 工具批处理），",
        "tools/ 是手（工具操作文件/网络/代码/git），cache_context.py 是记忆（三段式上下文），",
        "guard.py 是免疫系统（崩溃回滚 + 重试）。",
        "",
        "### 完整文档",
        "项目根目录 AGENTS.md 包含准确的架构文件清单、常用命令、设计决策。",
        "随时用 read_file('AGENTS.md') 查看最新版——它不塞 prefix，按需读取。",
        "进化/研究/记忆/RAG 是上层能力——它们通过工具暴露给 LLM，LLM 自主决定何时调用。",
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
        "2. 构建 CacheContext prefix（env msg + 项目上下文 + RAG 状态）",
        "3. 注入覆盖率门禁摘要到 log",
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
        "共 19 条关键连接关系：",
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
        from ..llm import get_current_model_name, _session_model
        lines.append(f"模型: {get_current_model_name()} | 粘性: {_session_model or '未锁定（下次调用自动选 Pro）'}")
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

    # 覆盖率
    try:
        from ..coverage_gate import get_overall_coverage, get_tier_summary
        cov = get_overall_coverage()
        lines.append(f"测试覆盖率: {cov:.0%}")
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
# 辅助
# ═══════════════════════════════════════════════════════════════

def _render_tree(path: Path, prefix: str, root: str) -> list[str]:
    result: list[str] = []
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return result
    for i, entry in enumerate(entries):
        if entry.name.startswith("__pycache__"):
            continue
        if entry.name.startswith(".") and entry.name != ".gitkeep":
            continue
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        result.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            ext = "    " if is_last else "│   "
            result.extend(_render_tree(entry, prefix + ext, root))
    return result
