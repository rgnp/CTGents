"""科研工具集：论文分析、交叉验证、卡片管理。

包装 knowledge/trajectory-prediction/tools/ 下的 paper_analyzer.py 和
cross_validator.py，让 agent 可以直接调用。新增 save_paper_card 实现
卡片持久化，打通"读论文→分析→存卡片"全流程。
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── 路径准备：将知识库工具目录加入搜索路径 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_KNOWLEDGE_TOOLS = _PROJECT_ROOT / "knowledge" / "trajectory-prediction" / "tools"
_KNOWLEDGE_PAPERS = _PROJECT_ROOT / "knowledge" / "trajectory-prediction" / "papers"

if str(_KNOWLEDGE_TOOLS) not in sys.path:
    sys.path.insert(0, str(_KNOWLEDGE_TOOLS))

# ── 工具定义 ──────────────────────────────────────────────

TOOLS_RESEARCH = [
    {
        "_meta": {"label": "分析论文", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "analyze_paper",
            "description": (
                "分析论文：方法论分类（6种类型）+ 已知Gap匹配 + 生成论文卡片模板。"
                "输入标题和摘要即可，可选补充作者/会议/年份完善卡片。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "论文标题"},
                    "abstract": {"type": "string", "description": "论文摘要"},
                    "contributions": {
                        "type": "string",
                        "description": "核心贡献（可选，不传则仅用标题+摘要分类）",
                    },
                    "authors": {"type": "string", "description": "作者（可选）"},
                    "conference": {"type": "string", "description": "会议/期刊（可选）"},
                    "year": {"type": "string", "description": "年份（可选）"},
                },
                "required": ["title", "abstract"],
            },
        },
    },
    {
        "_meta": {"label": "交叉验证", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "cross_validate",
            "description": (
                "新论文与知识库已有论文的交叉验证：矛盾检测、互补分析、"
                "Gap影响评估、方法论对比。自动读取 papers/ 下所有卡片。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "论文标题"},
                    "abstract": {"type": "string", "description": "论文摘要"},
                    "contributions": {
                        "type": "string",
                        "description": "核心贡献（可选）",
                    },
                },
                "required": ["title", "abstract"],
            },
        },
    },
    {
        "_meta": {
            "label": "保存论文卡片",
            "plan_blocked": True,
            "dedup_blacklist": True,
        },
        "type": "function",
        "function": {
            "name": "save_paper_card",
            "description": (
                "将论文卡片 Markdown 保存到 knowledge/trajectory-prediction/papers/。"
                "文件名建议格式：'年份-简称.md'（如 '2025-ssl-interactions.md'）。"
                "保存后提醒更新 KNOWLEDGE_INDEX.md。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "文件名，如 '2025-ssl-interactions.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "卡片 Markdown 完整内容",
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
]


# ── 工具实现 ──────────────────────────────────────────────


def analyze_paper(
    title: str,
    abstract: str,
    contributions: str = "",
    authors: str = "",
    conference: str = "",
    year: str = "",
) -> str:
    """分析论文：方法论分类 + Gap 匹配 + 卡片模板生成。"""
    try:
        from paper_analyzer import classify_methodology, generate_card, match_gaps
    except ImportError as e:
        return (
            f"无法导入 paper_analyzer: {e}\n"
            "请确认 knowledge/trajectory-prediction/tools/paper_analyzer.py 存在。"
        )

    methodology = classify_methodology(title, abstract, contributions)
    gaps = match_gaps(title, abstract, contributions)

    parts: list[str] = []

    # ── 方法论分类 ──
    primary = methodology["primary"]
    parts.append("## 方法论分类")
    parts.append(
        f"- **类型**: {primary['label']}（置信度 {primary['confidence']:.0%}）"
    )
    parts.append(f"- **说明**: {primary['description']}")
    parts.append(f"- **创新等级**: {primary['innovation_level']}")
    parts.append(f"- **风险**: {primary['risk']}")
    parts.append(f"- **同类论文**: {', '.join(primary['examples'])}")
    if "secondary" in methodology:
        sec = methodology["secondary"]
        parts.append(
            f"- **次级类型**: {sec['label']}（置信度 {sec['confidence']:.0%}）"
        )

    # ── Gap 匹配 ──
    parts.append("\n## Gap 匹配")
    if gaps:
        for g in gaps:
            parts.append(
                f"- **{g['gap']}** [{g['status']}]: {g['impact']} "
                f"（命中 {g['hit_count']} 个关键词: "
                f"{', '.join(g['hit_keywords'][:5])}）"
            )
    else:
        parts.append("⚠️ 未命中任何已知 Gap → 可能是新的空白方向！")

    # ── 卡片模板 ──
    parts.append("\n## 论文卡片模板\n")
    card = generate_card(
        title, authors, conference, year,
        abstract, contributions, methodology, gaps,
    )
    parts.append(card)

    return "\n".join(parts)


def cross_validate(
    title: str,
    abstract: str,
    contributions: str = "",
) -> str:
    """跨论文交叉验证：矛盾、互补、Gap 影响、方法论对比。"""
    try:
        from cross_validator import generate_report
    except ImportError as e:
        return (
            f"无法导入 cross_validator: {e}\n"
            "请确认 knowledge/trajectory-prediction/tools/cross_validator.py 存在。"
        )

    try:
        report = generate_report(title, abstract, contributions)
    except Exception as e:
        return f"交叉验证异常: {e}"

    if isinstance(report, dict) and "error" in report:
        return f"交叉验证失败: {report['error']}"

    return str(report)


def save_paper_card(filename: str, content: str) -> str:
    """保存论文卡片到知识库 papers/ 目录。"""
    filepath = _KNOWLEDGE_PAPERS / filename

    # 安全检查：确保写入路径在 papers/ 内
    try:
        filepath = filepath.resolve()
    except (OSError, RuntimeError):
        return f"文件名无效: {filename}"

    if not str(filepath).startswith(str(_KNOWLEDGE_PAPERS.resolve())):
        return f"拒绝写入: {filename} 不在知识库 papers/ 目录内"

    filepath.parent.mkdir(parents=True, exist_ok=True)

    try:
        filepath.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"保存失败: {e}"

    return (
        f"✅ 论文卡片已保存\n"
        f"路径: knowledge/trajectory-prediction/papers/{filename}\n"
        f"大小: {len(content)} 字符\n"
        f"⚠️  记得更新 KNOWLEDGE_INDEX.md 的论文列表。"
    )


# ── 调度 ──────────────────────────────────────────────────


def execute(name: str, args: dict) -> str | None:
    if name == "analyze_paper":
        return analyze_paper(
            title=args["title"],
            abstract=args["abstract"],
            contributions=args.get("contributions", ""),
            authors=args.get("authors", ""),
            conference=args.get("conference", ""),
            year=args.get("year", ""),
        )
    if name == "cross_validate":
        return cross_validate(
            title=args["title"],
            abstract=args["abstract"],
            contributions=args.get("contributions", ""),
        )
    if name == "save_paper_card":
        return save_paper_card(
            filename=args["filename"],
            content=args["content"],
        )
    return None
