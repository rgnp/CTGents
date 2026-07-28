"""论文工具：read_paper 解析 PDF 正文；fetch_paper 下载；transcribe_paper 转写落盘。

fetch_paper / transcribe_paper 是 paper-pipeline 阶段 1/2 的"去利刃"版：原先 skill
指导 agent 用 run_python 即兴写 requests/fitz 代码——有人会话里能跑，但无人期
（heartbeat worker）白名单刻意不给 run_python（任意代码=无边界）。把这两步机械
动作沉淀成窄工具：下载只进 knowledge/ 且校验 PDF 魔数/大小，转写只从项目内 PDF
到 knowledge/ 下 .md——两种模式共用，有人会话也不必再即兴写下载代码。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

from ..paths import KNOWLEDGE_DIR, resolve_runtime_path

MAX_CHARS = 30_000  # 单次返回最大字符数，超出截断
PAGE_SEP = "\n\n--- 第 {page} 页 ---\n\n"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_MIN_PDF_BYTES = 50_000          # 小于此值多半是错误页/占位页，不是论文
_MAX_PDF_BYTES = 80 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 90.0
_UA = "Mozilla/5.0 (compatible; ResearchBot/1.0)"


TOOLS_PAPER = [
    {
        "_meta": {"label": "下载论文", "group": "research", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "fetch_paper",
            "description": (
                "下载论文 PDF 到 knowledge/ 下（自动校验是有效 PDF）。"
                "source 支持 arXiv ID（如 2401.12345）或 https:// 的 PDF 直链。"
                "下载后用 read_paper 读取或 transcribe_paper 转写。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "arXiv ID（如 2401.12345）或 https PDF 直链",
                    },
                    "dest": {
                        "type": "string",
                        "description": "项目内目标路径，须在 knowledge/ 下且以 .pdf 结尾，"
                                       "如 knowledge/paper/<slug>/paper.pdf",
                    },
                },
                "required": ["source", "dest"],
            },
        },
    },
    {
        "_meta": {"label": "转写论文", "group": "research", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "transcribe_paper",
            "description": (
                "把项目内的论文 PDF 全文转写成 Markdown 落盘（逐页，页头 '## Page N'）。"
                "paper-pipeline 阶段 2 的机械转写步骤。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {
                        "type": "string",
                        "description": "项目内 PDF 路径，如 knowledge/paper/<slug>/paper.pdf",
                    },
                    "dest": {
                        "type": "string",
                        "description": "输出 Markdown 路径，须在 knowledge/ 下且以 .md 结尾，"
                                       "如 knowledge/paper/<slug>/paper.md",
                    },
                },
                "required": ["src", "dest"],
            },
        },
    },
    {
        "_meta": {"label": "读论文", "parallel_safe": True, "group": "research"},
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


def _resolve_in_project(rel: str, *, root_dir: str, suffix: str) -> tuple[Path | None, str]:
    """把虚拟相对路径解析进个人知识库，校验后缀。返回 (path, 错误文案)。"""
    try:
        target = resolve_runtime_path(rel, _PROJECT_ROOT)
        target.relative_to(KNOWLEDGE_DIR)
    except (ValueError, OSError):
        return None, f"❌ 路径必须位于个人 workspace 的 {root_dir}/ 下，收到 {rel!r}。"
    parts = Path(rel).parts
    if not parts or parts[0] != root_dir:
        return None, f"❌ 路径必须位于 {root_dir}/ 下，收到 {rel!r}。"
    if target.suffix.lower() != suffix:
        return None, f"❌ 路径必须以 {suffix} 结尾，收到 {rel!r}。"
    return target, ""


def _download(url: str) -> bytes:
    """按 UA/超时下载，读到大小上限即止。独立函数便于测试打桩。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:  # noqa: S310  https-only 由调用方校验
        return resp.read(_MAX_PDF_BYTES + 1)


def fetch_paper(source: str, dest: str) -> str:
    """下载论文 PDF 到 knowledge/ 下；校验 https 来源、PDF 魔数与大小。"""
    src = (source or "").strip()
    if _ARXIV_ID_RE.match(src):
        url = f"https://arxiv.org/pdf/{src}.pdf"
    elif src.startswith("https://"):
        url = src
    else:
        return "❌ source 需为 arXiv ID（如 2401.12345）或 https:// 的 PDF 直链。"

    target, err = _resolve_in_project(dest, root_dir="knowledge", suffix=".pdf")
    if err:
        return err + " 推荐 knowledge/paper/<slug>/paper.pdf。"

    try:
        data = _download(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"❌ 下载失败: {exc}（可稍后重试；连续失败按 pipeline 规则记 download_error）"
    if len(data) > _MAX_PDF_BYTES:
        return f"❌ 文件超过 {_MAX_PDF_BYTES // (1024 * 1024)}MB 上限，拒绝落盘: {url}"
    if len(data) < _MIN_PDF_BYTES or not data.startswith(b"%PDF"):
        return (f"❌ 下载内容不是有效 PDF（{len(data)} 字节，魔数 {data[:5]!r}）——"
                "可能是错误页/被拦截，不落盘。")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return f"✅ 已下载 {url} → {dest}（{len(data) // 1024} KB）。可用 read_paper 读取或 transcribe_paper 转写。"


def transcribe_paper(src: str, dest: str) -> str:
    """PDF 全文逐页转写成 Markdown 落盘（页头 '## Page N'），返回行数/页数统计。"""
    pdf_path, err = _resolve_in_project(src, root_dir="knowledge", suffix=".pdf")
    if err:
        return err
    if not pdf_path.exists():
        return f"❌ PDF 不存在: {src}"
    target, err = _resolve_in_project(dest, root_dir="knowledge", suffix=".md")
    if err:
        return err + " 推荐 knowledge/paper/<slug>/paper.md。"

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:  # noqa: BLE001  pymupdf 异常类型不稳定，统一转人话
        return f"❌ 打开 PDF 失败: {src} — {exc}"
    try:
        pages = [f"## Page {page.number + 1}\n\n{page.get_text()}" for page in doc]
    finally:
        doc.close()

    text = "\n\n".join(pages)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    n_lines = text.count("\n") + 1
    return (f"✅ 已转写 {src} → {dest}（{len(pages)} 页，{n_lines} 行，{len(text)} 字符）。"
            "质量检查（行数/Abstract/Introduction/Conclusion）由你按 pipeline 清单核对。")


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
    if name == "fetch_paper":
        return fetch_paper(args["source"], args["dest"])
    if name == "transcribe_paper":
        return transcribe_paper(args["src"], args["dest"])
    return None
