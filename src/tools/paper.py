"""论文工具：read_paper —— 解析 PDF 正文。"""

from pathlib import Path

import pymupdf

MAX_CHARS = 30_000  # 单次返回最大字符数，超出截断
PAGE_SEP = "\n\n--- 第 {page} 页 ---\n\n"


TOOLS_PAPER = [
    {
        "_meta": {"label": "读论文", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "read_paper",
            "description": (
                "读取论文 PDF 全文，提取正文内容。"
                "支持本地 PDF 文件（相对/绝对路径均可）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "PDF 文件路径，如 'papers/attention.pdf'",
                    },
                    "start_page": {
                        "type": "integer",
                        "description": "起始页码（从 1 开始），不传 = 第 1 页",
                    },
                    "end_page": {
                        "type": "integer",
                        "description": "结束页码（含），不传 = 最后一页",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


def read_paper(path: str, start_page: int | None = None, end_page: int | None = None) -> str:
    """读取 PDF 正文，逐页提取文本，按页分隔返回。"""
    # ── 路径解析 ──
    filepath = Path(path).expanduser()
    if not filepath.is_absolute():
        filepath = Path.cwd() / filepath
    filepath = filepath.resolve()

    if not filepath.exists():
        return f"文件不存在: {filepath}"
    if not filepath.is_file():
        return f"路径不是文件: {filepath}"
    if filepath.suffix.lower() != ".pdf":
        return f"不是 PDF 文件（后缀为 {filepath.suffix}）: {filepath}"

    # ── 打开 PDF ──
    try:
        doc = pymupdf.open(str(filepath))
    except pymupdf.FileDataError:
        return f"文件损坏或不是有效的 PDF: {filepath}"
    except Exception as exc:
        return f"打开 PDF 失败: {filepath} — {exc}"

    total_pages = len(doc)

    # ── 页码范围 ──
    sp = 1
    ep = total_pages
    if start_page is not None:
        sp = max(1, start_page)
    if end_page is not None:
        ep = min(total_pages, end_page)
    if sp > ep:
        doc.close()
        return f"页码范围无效: start_page={start_page}, end_page={end_page}, 总页数={total_pages}"

    # ── 提取正文 ──
    parts: list[str] = [f"文件: {path}  |  共 {total_pages} 页  |  显示第 {sp}-{ep} 页\n"]
    total_chars = 0
    truncated = False

    for i in range(sp - 1, ep):
        page = doc[i]
        text = page.get_text()
        if not text:
            text = "(此页无文本内容)"

        sep = PAGE_SEP.format(page=i + 1)
        block = sep + text

        # 截断检查
        if total_chars + len(block) > MAX_CHARS:
            remaining = MAX_CHARS - total_chars
            if remaining > 200:
                block = block[:remaining] + "\n\n[已截断：超出最大字符限制]"
            parts.append(block)
            truncated = True
            break
        parts.append(block)
        total_chars += len(block)

    doc.close()

    result = "".join(parts)
    if truncated:
        result += f"\n\n⚠ 已截断至 {MAX_CHARS} 字符。如需完整内容，请用 start_page/end_page 分批读取。"
    return result


def execute(name: str, args: dict) -> str | None:
    if name == "read_paper":
        return read_paper(
            args["path"],
            args.get("start_page"),
            args.get("end_page"),
        )
    return None
