"""经验检索：搜索 tasks/archive/ 中的相似历史任务，提取教训注入当前任务上下文。

CORPGEN ablation 结论：experiential learning（成功轨迹→规范模式→few-shot 检索）
在所有架构组件中贡献最大。本模块让"有存档"变成"用存档"。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "tasks" / "archive"


def _tokenize(text: str) -> set[str]:
    """分词：英文按词、中文按字符 bigram，保证跨语言可比较。"""
    tokens: set[str] = set()
    # 英文/数字：按词
    for word in re.findall(r'[a-z0-9]+', text.lower()):
        if len(word) >= 2:
            tokens.add(word)
    # 中文：字符 bigram（"记忆腐败" → {"记忆","忆腐","腐败"}）
    cjk = re.findall(r'[\u4e00-\u9fff]', text.lower())
    for i in range(len(cjk) - 1):
        tokens.add(cjk[i] + cjk[i + 1])
    return tokens


def _parse_archive_file(path: Path) -> dict | None:
    """解析单个 archive 文件，提取结构化信息。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    lines = text.splitlines()
    title = ""
    for line in lines:
        if line.startswith("# ") and "目标锚点" not in line:
            title = line[2:].strip()
            break

    anchor = ""
    in_anchor = False
    for line in lines:
        stripped = line.strip()
        if in_anchor:
            if not stripped or stripped.startswith("#"):
                break
            anchor += " " + stripped
        elif stripped == "# 目标锚点":
            in_anchor = True
    anchor = anchor.strip()

    summary = ""
    in_summary = False
    for line in lines:
        if line.strip() == "## 完成总结":
            in_summary = True
        elif in_summary and line.startswith("#"):
            break
        elif in_summary:
            summary += line + "\n"
    summary = summary.strip()

    search_text = f"{title} {anchor} {summary}"

    total_steps = sum(1 for line in lines if line.strip().startswith("- ["))
    done_steps = sum(
        1 for line in lines
        if line.strip().startswith("- [x]") or line.strip().startswith("- [X]")
    )

    return {
        "name": path.stem,
        "title": title,
        "anchor": anchor,
        "summary": summary,
        "search_text": search_text,
        "total_steps": total_steps,
        "done_steps": done_steps,
    }


def _load_archives() -> list[dict]:
    """加载全部 archive 文件（按文件名排序）。"""
    if not ARCHIVE_DIR.is_dir():
        return []
    archives = []
    for f in sorted(ARCHIVE_DIR.glob("*.md")):
        parsed = _parse_archive_file(f)
        if parsed:
            archives.append(parsed)
    return archives


def search_similar_tasks(task_description: str, top_k: int = 3) -> list[dict]:
    """搜索与 task_description 最相似的历史任务（Jaccard 相似度）。"""
    archives = _load_archives()
    if not archives:
        return []

    query_tokens = _tokenize(task_description)
    if not query_tokens:
        return []

    scored = []
    for a in archives:
        doc_tokens = _tokenize(a["search_text"])
        if not doc_tokens:
            continue
        intersection = query_tokens & doc_tokens
        union = query_tokens | doc_tokens
        score = len(intersection) / len(union) if union else 0
        if score > 0:
            scored.append((score, a))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in scored[:top_k]]


def format_experience_context(tasks: list[dict]) -> str | None:
    """格式化为上下文注入文本。"""
    if not tasks:
        return None

    lines = [
        "📚 历史类似任务（从 tasks/archive/ 检索，供参考）：",
        "",
    ]
    for i, t in enumerate(tasks, 1):
        title_text = t["title"] or t["name"]
        lines.append(f"### {i}. {title_text}")
        if t["anchor"]:
            lines.append(f"目标：{t['anchor']}")
        if t["summary"]:
            lines.append(t["summary"])
        else:
            lines.append(f"（{t['done_steps']}/{t['total_steps']} 步完成，无书面总结）")
        lines.append("")

    lines.append(
        "💡 上方是你自己在过去类似任务中总结的教训，这次可以参考——不是抄方案，"
        "是避免踩同样的坑。"
    )
    return "\n".join(lines)
