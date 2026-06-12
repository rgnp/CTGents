"""知识获取工具 — 对话中发现知识缺口时一键搜论文、读摘要。

编排现有工具：scan_papers → read_papers，返回结构化摘要供 agent 判断下一步。
"""

from __future__ import annotations

import time

from .research import _arxiv_api_search, _read_arxiv_abstract

_MAX_RESULTS = 10
_BATCH_SLEEP = 0.8


TOOLS_LEARN = [
    {
        "_meta": {"label": "学习", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "learn",
            "description": (
                "搜索最新论文补知识缺口。遇到不确定的概念/技术名词时调用——"
                "搜 arxiv 最新论文、读摘要、返回结构化总结。比 search_web 更适合科研。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "自然语言主题，如 'trajectory prediction with diffusion models'",
                    },
                },
                "required": ["topic"],
            },
        },
    },
]


def learn(topic: str) -> str:
    """搜索最新论文并返回摘要。编排 scan_papers → read_papers 链路。"""
    if not topic or not topic.strip():
        return "请提供搜索主题。"

    topic = topic.strip()

    # Step 1: 搜 arxiv
    results = _arxiv_api_search(topic, max_results=_MAX_RESULTS)
    if not results:
        return f"在 arxiv 上未找到与 '{topic}' 相关的近期论文。换个搜索词试试。"

    # Step 2: 读摘要
    ids = [aid for aid, _date in results]
    papers: dict[str, dict] = {}
    for aid in ids:
        try:
            papers[aid] = _read_arxiv_abstract(aid)
            time.sleep(_BATCH_SLEEP)
        except Exception:
            papers[aid] = {"id": aid, "title": "FETCH_ERR", "abstract": "读取失败", "venue": "?"}

    # Step 3: 构建结构化输出
    lines = [f"📚 搜索 '{topic}' → {len(papers)} 篇最新论文（arxiv，按提交日期倒序）\n"]
    lines.append("| # | arxiv | 标题 | 会议 |")
    lines.append("|:--:|------|------|:---:|")

    for j, (aid, info) in enumerate(papers.items(), 1):
        title = info.get("title", "?")[:80]
        venue = info.get("venue", "arxiv")
        lines.append(f"| {j} | [{aid}](https://arxiv.org/abs/{aid}) | {title} | {venue} |")

    lines.append("")
    for j, (aid, info) in enumerate(papers.items(), 1):
        title = info.get("title", "?")
        abstract = info.get("abstract", "")
        if not abstract or abstract == "读取失败":
            continue
        lines.append(f"### #{j} {title}")
        lines.append(f"{abstract[:600]}{'...' if len(abstract) > 600 else ''}")
        lines.append(f"[arxiv](https://arxiv.org/abs/{aid}) | "
                      f"全文用 `read_paper` 下载后读取")
        lines.append("")

    lines.append(
        "💡 想深入了解某篇，告诉我编号。"
        "我会用 read_paper 读全文 → analyze_paper 分析 → save_paper_card 存知识库。"
    )
    lines.append(
        "\n💡 有价值的发现？用 write_file 保存到 knowledge/search/<主题>.md，"
        "以后 rag_search 可检索。"
    )

    return "\n".join(lines)


def execute(name: str, args: dict) -> str | None:
    if name == "learn":
        return learn(args["topic"])
    return None
