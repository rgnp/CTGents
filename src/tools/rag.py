"""RAG 检索增强生成：项目代码自动索引 + 语义搜索。

功能：
  1. rag_index — 扫描项目文件，智能分块，建立 TF-IDF 加权索引
  2. rag_query — 语义搜索代码库，返回最相关的代码片段
  3. rag_status — 查看索引状态

自动注入：
  - main.py 启动时加载索引摘要注入 system prompt
  - 对话中检测到代码相关问题自动检索上下文

设计原则：
  - 零额外依赖（纯 Python + 标准库）
  - TF-IDF + 代码语义关键词加权，无需 embedding API
  - 按文件类型智能分块（函数/类/行数）
  - 增量更新：只重新索引变更的文件
"""

import json
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

# 索引目录（存放在项目根目录）
RAG_INDEX_DIR = ".rag-index"

# 索引文件名
RAG_INDEX_FILE = "index.json"
RAG_META_FILE = "meta.json"

# 分块配置
MAX_CHUNK_LINES = 50        # 非结构化文件每块最大行数
MIN_CHUNK_LINES = 10        # 最小块行数
MAX_CHUNK_CHARS = 2000      # 每块最大字符数

# 搜索配置
DEFAULT_TOP_K = 5           # 默认返回前 N 个结果
SEARCH_MIN_SCORE = 0.05     # 最低匹配分数

# 关键词权重
WEIGHT_NAME = 3.0           # 函数名/类名
WEIGHT_COMMENT = 2.0        # 注释/docstring
WEIGHT_CODE = 1.0           # 代码正文
WEIGHT_IDENTIFIER = 1.5     # 标识符（变量名等）

# 自动注入配置
AUTO_RETRIEVE_TOP_K = 3     # 自动检索时返回的块数
AUTO_RETRIEVE_MAX_CHARS = 1500  # 自动注入的最大字符数

# 增量更新：缓存文件 hash，跳过未变更的文件
HASH_CACHE_FILE = "hashes.json"

# 文件大小限制（超过此大小的文件跳过索引，单位字节）
MAX_FILE_SIZE = 512 * 1024  # 512KB

# ═══════════════════════════════════════════════════════════════
# 支持的源文件扩展名
# ═══════════════════════════════════════════════════════════════

SOURCE_EXTENSIONS: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    # JavaScript / TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # Web
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    # Java / JVM
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # C / C++
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    # C#
    ".cs": "csharp",
    # Ruby
    ".rb": "ruby",
    # PHP
    ".php": "php",
    # Shell
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    # Config / Data
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".rst": "rst",
    # Swift
    ".swift": "swift",
    # Lua
    ".lua": "lua",
    # R
    ".r": "r",
    # Dart
    ".dart": "dart",
}

# ═══════════════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════════════

TOOLS_RAG = [
    {
        "type": "function",
        "function": {
            "name": "rag_index",
            "description": (
                "索引项目代码库：扫描项目文件，智能分块，建立语义索引。"
                "首次使用 RAG 前必须调用一次。之后可随时调用以增量更新。"
                "支持 30+ 种编程语言。增量更新只重新索引变更的文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前项目目录",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "是否强制全量重建索引（默认 False，只增量更新）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_query",
            "description": (
                "在已索引的项目代码库中执行语义搜索，返回最相关的代码片段。"
                "搜索前需先调用 rag_index 建立索引。"
                "比 grep_code 更智能——理解代码语义、函数用途、注释含义。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或自然语言描述，如 '文件读写功能'、'如何调用 LLM API'、'safety check 逻辑'",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回最相关的前 N 个结果，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_status",
            "description": "查看项目 RAG 索引状态：已索引文件数、覆盖语言、索引大小、上次更新时间。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ═══════════════════════════════════════════════════════════════
# 路径工具
# ═══════════════════════════════════════════════════════════════


def _find_project_root(path: str | None = None) -> Path:
    """找到项目根目录（包含 .git 或 pyproject.toml 的目录）。"""
    start = Path(path or os.getcwd()).resolve()
    # 从当前目录往上找
    for p in [start] + list(start.parents):
        if (p / ".git").exists() or (p / "pyproject.toml").exists():
            return p
    return start


def _rag_dir(project_root: Path) -> Path:
    """获取项目根目录下的 RAG 索引目录。"""
    d = project_root / RAG_INDEX_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(project_root: Path) -> Path:
    return _rag_dir(project_root) / RAG_INDEX_FILE


def _meta_path(project_root: Path) -> Path:
    return _rag_dir(project_root) / RAG_META_FILE


def _hash_cache_path(project_root: Path) -> Path:
    return _rag_dir(project_root) / HASH_CACHE_FILE


# ═══════════════════════════════════════════════════════════════
# 文件扫描
# ═══════════════════════════════════════════════════════════════

# 默认忽略的目录
_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", "env",
    ".env", "dist", "build", ".next", "target", "bin", "obj",
    ".rag-index", ".agent-memory", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "coverage", ".tox", ".eggs", "eggs",
    "site-packages", ".yarn", ".npm", "bower_components",
    "vendor", "third_party", "third-party",
}

# 默认忽略的文件
_IGNORE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".gitignore", ".gitattributes", ".editorconfig",
    "*.min.js", "*.min.css", "*.bundle.js",
}


def _should_ignore_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.startswith(".")


def _should_ignore_file(name: str, ext: str) -> bool:
    if name in _IGNORE_FILES:
        return True
    if ext in (".pyc", ".pyo", ".so", ".o", ".class", ".jar", ".war"):
        return True
    # 忽略测试缓存快照
    if "__snapshots__" in name or "__pycache__" in name:
        return True
    return False


def _scan_source_files(project_root: Path) -> list[Path]:
    """扫描项目根目录下所有支持的源文件。"""
    files: list[Path] = []
    try:
        for root, dirs, names in os.walk(project_root):
            # 跳过忽略目录
            dirs[:] = [d for d in dirs if not _should_ignore_dir(d)]

            # 跳过 .rag-index 目录本身
            rel_root = Path(root).relative_to(project_root)
            if str(rel_root) == RAG_INDEX_DIR:
                continue

            for name in names:
                ext = Path(name).suffix.lower()
                if ext not in SOURCE_EXTENSIONS:
                    continue
                if _should_ignore_file(name, ext):
                    continue
                fpath = Path(root) / name
                try:
                    if fpath.stat().st_size > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                files.append(fpath)
    except (OSError, PermissionError):
        pass
    return sorted(files)


def _get_file_hash(path: Path) -> str:
    """快速文件哈希（用 mtime + size 做缓存键，避免读全文件）。"""
    try:
        st = path.stat()
        return f"{st.st_mtime:.0f}-{st.st_size}"
    except OSError:
        return ""


def _read_hash_cache(project_root: Path) -> dict[str, str]:
    """读取上次索引时的文件哈希缓存。"""
    p = _hash_cache_path(project_root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_hash_cache(project_root: Path, hashes: dict[str, str]) -> None:
    """写入文件哈希缓存。"""
    _hash_cache_path(project_root).write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════
# 代码分块
# ═══════════════════════════════════════════════════════════════


# 语言 → (函数/类正则, 注释正则)
_CHUNK_PATTERNS: dict[str, tuple[str, str]] = {
    "python": (
        r"^(def |class |@property|async def |@\w+\.setter|@\w+\.deleter)",
        r"(^\s*#.*$|'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\")",
    ),
    "javascript": (
        r"^(function |class |export (default )?(function|class)|const \w+ = (\(|\(?)",
        r"(^\s*//.*$|/\*[\s\S]*?\*/)",
    ),
    "typescript": (
        r"^(function |class |interface |type |enum |export (default )?(function|class|interface|type|enum))",
        r"(^\s*//.*$|/\*[\s\S]*?\*/)",
    ),
    "go": (
        r"^(func |type |struct |interface )",
        r"(^\s*//.*$|/\*[\s\S]*?\*/)",
    ),
    "rust": (
        r"^(fn |struct |enum |impl |trait |pub (fn|struct|enum|impl|trait|mod))",
        r"(^\s*//.*$|///.*$|/\*[\s\S]*?\*/)",
    ),
    "java": (
        r"^(public |private |protected )?(static )?(class |interface |enum |void |\w+ )?\w+\(|@\w+)",
        r"(^\s*//.*$|/\*[\s\S]*?\*/)",
    ),
    "cpp": (
        r"^(class |struct |enum |namespace |template |using |void |int |bool |std::)",
        r"(^\s*//.*$|/\*[\s\S]*?\*/)",
    ),
}

# 默认分块模式
_DEFAULT_FN_PATTERN = r"^(def |function |class |func |fn |pub )"
_DEFAULT_COMMENT_PATTERN = r"(^\s*#.*$|^\s*//.*$|/\*[\s\S]*?\*/)"


def _get_chunk_patterns(language: str) -> tuple[str, str]:
    """获取指定语言的函数/类分割正则和注释正则。"""
    patterns = _CHUNK_PATTERNS.get(language)
    if patterns:
        return patterns
    return _DEFAULT_FN_PATTERN, _DEFAULT_COMMENT_PATTERN


def _extract_identifiers(text: str) -> list[str]:
    """从代码中提取标识符（函数名、变量名、类名等）。"""
    # 驼峰命名拆词:  camelCase → camel, case
    # 蛇形命名拆词:  snake_case → snake, case
    identifiers: list[str] = []

    # 找所有标识符
    id_matches = re.findall(r'\b([a-zA-Z_]\w*)\b', text)
    for ident in id_matches:
        # 跳过纯数字、太短的、Python 关键字
        if len(ident) < 2 or ident in (
            'if', 'for', 'while', 'and', 'or', 'not', 'in', 'is',
            'def', 'class', 'return', 'import', 'from', 'as',
            'self', 'None', 'True', 'False', 'raise', 'try',
            'except', 'finally', 'with', 'yield', 'lambda', 'pass',
            'break', 'continue', 'elif', 'else', 'assert', 'del',
            'global', 'nonlocal', 'var', 'let', 'const', 'function',
            'export', 'default', 'extends', 'implements', 'new',
            'this', 'super', 'typeof', 'void', 'public', 'private',
            'protected', 'static', 'final', 'abstract', 'synchronized',
            'package', 'namespace', 'using', 'include', 'struct',
            'enum', 'trait', 'impl', 'fn', 'pub', 'mut', 'let',
            'match', 'where', 'async', 'await',
        ):
            continue
        identifiers.append(ident)

        # 驼峰拆词
        if re.search(r'[a-z][A-Z]', ident):
            parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', ident)
            identifiers.extend(p for p in parts if len(p) >= 2)

        # 蛇形拆词
        if '_' in ident:
            identifiers.extend(p for p in ident.split('_') if len(p) >= 2)

    return identifiers


def _extract_keywords_from_query(query: str) -> list[str]:
    """从自然语言查询中提取关键词。"""
    # 去除停用词
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'can', 'shall',
        'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
        'as', 'into', 'through', 'during', 'before', 'after',
        'above', 'below', 'between', 'out', 'off', 'over', 'under',
        'again', 'further', 'then', 'once', 'here', 'there',
        'when', 'where', 'why', 'how', 'all', 'each', 'every',
        'both', 'few', 'more', 'most', 'other', 'some', 'such',
        'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
        'too', 'very', 'just', 'also', 'about', 'up', 'down',
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
        '都', '一', '一个', '上', '也', '很', '到', '说', '要',
        '去', '你', '会', '着', '没有', '看', '好', '自己', '这',
        '他', '她', '它', '们', '那', '什么', '怎么', '如何',
        '为什么', '哪个', '哪些', '谁', '哪里', '多少',
    }

    # 分词
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) >= 2]

    # 额外：提取可能的标识符（驼峰/蛇形）
    identifiers = re.findall(r'\b[A-Za-z_]\w*[a-z][A-Z]\w*\b|\b[a-z]+_[a-z]\w*\b', query)
    keywords.extend(identifiers)

    return list(set(keywords))


class CodeChunk:
    """代码块：文件中的一个功能单元（函数/类/代码段）。"""

    def __init__(
        self,
        file_path: str,
        language: str,
        chunk_type: str,
        name: str,
        start_line: int,
        end_line: int,
        content: str,
        identifiers: list[str] | None = None,
    ):
        self.file_path = file_path
        self.language = language
        self.chunk_type = chunk_type  # 'function', 'class', 'block'
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.content = content
        self.identifiers = identifiers or _extract_identifiers(content)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "chunk_type": self.chunk_type,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "identifiers": self.identifiers,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CodeChunk":
        return cls(**d)

    @property
    def short_path(self) -> str:
        """返回简短文件路径（相对于项目根）。"""
        return self.file_path

    @property
    def summary(self) -> str:
        """返回块摘要（文件名+行号+名称）。"""
        return f"{self.file_path}:{self.start_line}-{self.end_line} [{self.chunk_type}] {self.name}"


def _chunk_python_file(file_path: Path, content: str, language: str) -> list[CodeChunk]:
    """按函数/类分割 Python 文件。"""
    lines = content.split("\n")
    chunks: list[CodeChunk] = []
    i = 0
    fn_pattern, _ = _get_chunk_patterns(language)

    while i < len(lines):
        line = lines[i]
        m = re.match(fn_pattern, line)
        if m:
            # 找到函数/类定义，收集到下一个函数/类或文件尾
            name_match = re.search(
                r'(?:def |class |async def )(\w+)',
                line,
            )
            name = name_match.group(1) if name_match else "anonymous"
            chunk_type = "class" if line.strip().startswith("class") else "function"

            start = i
            i += 1
            # 收集函数体（缩进级别判断）
            if chunk_type == "function":
                # 函数体：找到缩进块
                while i < len(lines):
                    if re.match(fn_pattern, lines[i]) and not lines[i].startswith((" " * 4, "\t")):
                        break
                    i += 1
            else:
                # 类体：到下一个类或文件尾
                while i < len(lines):
                    if re.match(r"^class ", lines[i]):
                        break
                    i += 1
            end = i

            chunk_content = "\n".join(lines[start:end])
            if len(chunk_content) > MAX_CHUNK_CHARS:
                # 超大块，按子函数再分
                sub_chunks = _split_large_chunk(
                    file_path, content, language,
                    chunk_content, start, name,
                )
                chunks.extend(sub_chunks)
            else:
                chunks.append(CodeChunk(
                    file_path=str(file_path),
                    language=language,
                    chunk_type=chunk_type,
                    name=name,
                    start_line=start + 1,
                    end_line=end,
                    content=chunk_content,
                ))
        else:
            i += 1

    # 如果文件没有函数/类，整体作为一个块
    if not chunks:
        chunks.append(CodeChunk(
            file_path=str(file_path),
            language=language,
            chunk_type="block",
            name=file_path.name,
            start_line=1,
            end_line=len(lines),
            content=content,
        ))

    return chunks


def _chunk_generic_file(file_path: Path, content: str, language: str) -> list[CodeChunk]:
    """通用分块：按行数切割。"""
    lines = content.split("\n")
    chunks: list[CodeChunk] = []
    fn_pattern, _ = _get_chunk_patterns(language)

    # 先尝试按函数/类分割
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(fn_pattern, line)
        if m:
            name_match = re.search(r'(?:function |func |fn |class )(\w+)', line)
            name = name_match.group(1) if name_match else "anonymous"
            chunk_type = "function" if "class" not in line else "class"
            start = i
            i += 1
            # 找到大括号或缩进块结束
            brace_count = 0
            while i < len(lines):
                if re.match(fn_pattern, lines[i]) and brace_count == 0:
                    break
                brace_count += lines[i].count("{") - lines[i].count("}")
                if brace_count < 0:
                    brace_count = 0
                i += 1
            end = i
            chunk_content = "\n".join(lines[start:end])
            if len(chunk_content) <= MAX_CHUNK_CHARS:
                chunks.append(CodeChunk(
                    file_path=str(file_path),
                    language=language,
                    chunk_type=chunk_type,
                    name=name,
                    start_line=start + 1,
                    end_line=end,
                    content=chunk_content,
                ))
                continue

        i += 1

    # 如果没有函数级分块，按行数切分
    if not chunks:
        for start in range(0, len(lines), MAX_CHUNK_LINES):
            end = min(start + MAX_CHUNK_LINES, len(lines))
            chunk_content = "\n".join(lines[start:end])
            if len(chunk_content.strip()) < MIN_CHUNK_LINES:
                # 太短的块合并到上一个
                if chunks:
                    prev = chunks[-1]
                    prev.end_line = end
                    prev.content += "\n" + chunk_content
                    prev.identifiers = _extract_identifiers(prev.content)
                continue
            chunks.append(CodeChunk(
                file_path=str(file_path),
                language=language,
                chunk_type="block",
                name=f"{file_path.name}:{start + 1}",
                start_line=start + 1,
                end_line=end,
                content=chunk_content,
            ))

    return chunks


def _split_large_chunk(
    file_path: Path, full_content: str, language: str,
    chunk_content: str, offset: int, parent_name: str,
) -> list[CodeChunk]:
    """拆分超大块（如巨大的类），按行数切分。"""
    lines = chunk_content.split("\n")
    chunks: list[CodeChunk] = []
    for start in range(0, len(lines), MAX_CHUNK_LINES):
        end = min(start + MAX_CHUNK_LINES, len(lines))
        sub_content = "\n".join(lines[start:end])
        chunks.append(CodeChunk(
            file_path=str(file_path),
            language=language,
            chunk_type="block",
            name=f"{parent_name}:{start + offset + 1}",
            start_line=start + offset + 1,
            end_line=end + offset,
            content=sub_content,
        ))
    return chunks


def _chunk_file(file_path: Path, language: str) -> list[CodeChunk]:
    """将文件分块。"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    if not content.strip():
        return []

    if language == "python":
        return _chunk_python_file(file_path, content, language)
    else:
        return _chunk_generic_file(file_path, content, language)


# ═══════════════════════════════════════════════════════════════
# TF-IDF 索引
# ═══════════════════════════════════════════════════════════════


class TfIdfIndex:
    """TF-IDF 索引：建立词 → (文档, 权重) 的倒排索引。"""

    def __init__(self):
        self.documents: list[CodeChunk] = []
        self.inverted_index: dict[str, list[tuple[int, float]]] = {}  # term → [(doc_id, weight)]
        self.doc_norms: list[float] = []  # 每个文档的 L2 范数
        self.num_docs: int = 0

    def add_document(self, chunk: CodeChunk) -> None:
        """向索引添加一个文档块。"""
        doc_id = self.num_docs
        self.documents.append(chunk)
        self.num_docs += 1

        # 提取词项并加权
        term_weights: Counter = Counter()

        # 1. 标识符（函数名、类名、变量名）→ 高权重
        for ident in chunk.identifiers:
            term_weights[ident.lower()] += WEIGHT_NAME

        # 2. 注释行 → 中等权重
        for line in chunk.content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("/*"):
                words = re.findall(r'\b\w+\b', stripped.lower())
                for w in words:
                    if len(w) >= 2:
                        term_weights[w] += WEIGHT_COMMENT

        # 3. 代码正文 → 基础权重
        code_text = chunk.content.lower()
        words = re.findall(r'\b\w+\b', code_text)
        for w in words:
            if len(w) >= 2:
                term_weights[w] += WEIGHT_CODE

        # 构建倒排索引
        total_terms = sum(term_weights.values())
        if total_terms == 0:
            self.doc_norms.append(0.0)
            return

        for term, weight in term_weights.items():
            # TF = 词频 / 总词数
            tf = weight / total_terms
            if term not in self.inverted_index:
                self.inverted_index[term] = []
            self.inverted_index[term].append((doc_id, tf))

        # 预计算文档范数（仅基于已添加的 tf 值）
        norm = math.sqrt(sum(tf * tf for _, tf in term_weights.items()))
        self.doc_norms.append(norm)

    def search(self, query_terms: list[str], top_k: int = DEFAULT_TOP_K) -> list[tuple[int, float]]:
        """搜索最相关的文档。返回 [(doc_id, score), ...]。"""
        if not self.num_docs:
            return []

        # 计算 IDF
        idf: dict[str, float] = {}
        for term in query_terms:
            tl = term.lower()
            if tl in self.inverted_index:
                df = len(self.inverted_index[tl])
                idf[tl] = math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0)
            else:
                idf[tl] = 0.0

        # 计算每个文档的 BM25 分数
        scores: list[float] = [0.0] * self.num_docs
        k1, b = 1.5, 0.75
        avg_dl = sum(len(d.content) for d in self.documents) / max(self.num_docs, 1)

        for term in query_terms:
            tl = term.lower()
            if tl not in self.inverted_index or idf[tl] == 0:
                continue
            for doc_id, tf in self.inverted_index[tl]:
                doc_len = len(self.documents[doc_id].content)
                # BM25 评分
                score = idf[tl] * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / max(avg_dl, 1)))
                scores[doc_id] += score

        # 按分数排序
        ranked = [(doc_id, score) for doc_id, score in enumerate(scores) if score > SEARCH_MIN_SCORE]
        ranked.sort(key=lambda x: -x[1])
        return ranked[:top_k]

    def to_dict(self) -> dict:
        """序列化为可 JSON 序列化的 dict。"""
        return {
            "documents": [d.to_dict() for d in self.documents],
            "inverted_index": {k: v for k, v in self.inverted_index.items()},
            "doc_norms": self.doc_norms,
            "num_docs": self.num_docs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TfIdfIndex":
        idx = cls()
        idx.documents = [CodeChunk.from_dict(doc) for doc in d["documents"]]
        idx.inverted_index = d["inverted_index"]
        idx.doc_norms = d["doc_norms"]
        idx.num_docs = d["num_docs"]
        return idx


# ═══════════════════════════════════════════════════════════════
# 索引构建
# ═══════════════════════════════════════════════════════════════


def _build_index(files: list[Path], project_root: Path) -> TfIdfIndex:
    """从文件列表构建 TF-IDF 索引。"""
    index = TfIdfIndex()
    for file_path in files:
        try:
            rel_path = str(file_path.relative_to(project_root))
        except ValueError:
            rel_path = str(file_path)

        ext = file_path.suffix.lower()
        language = SOURCE_EXTENSIONS.get(ext, "unknown")

        chunks = _chunk_file(file_path, language)
        for chunk in chunks:
            # 使用相对路径
            chunk.file_path = rel_path
            index.add_document(chunk)

    return index


def _load_index(project_root: Path) -> TfIdfIndex | None:
    """从磁盘加载索引。"""
    p = _index_path(project_root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return TfIdfIndex.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _save_index(project_root: Path, index: TfIdfIndex) -> None:
    """保存索引到磁盘。"""
    _index_path(project_root).write_text(
        json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save_meta(project_root: Path, stats: dict) -> None:
    """保存索引元信息。"""
    stats["updated_at"] = time.time()
    _meta_path(project_root).write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_meta(project_root: Path) -> dict:
    """加载索引元信息。"""
    p = _meta_path(project_root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════


def index_project(path: str | None = None, force: bool = False) -> str:
    """索引项目代码库。

    Args:
        path: 项目路径，默认当前目录
        force: 是否强制全量重建

    Returns:
        可读的索引结果报告
    """
    project_root = _find_project_root(path)
    files = _scan_source_files(project_root)

    if not files:
        return "⚠️ 未找到支持的源文件（支持 30+ 种编程语言）。"

    start_time = time.time()

    if force:
        # 全量重建
        index = _build_index(files, project_root)
        _save_index(project_root, index)
        # 写入当前文件哈希缓存，下次增量更新可正常检测变更
        hash_cache: dict[str, str] = {}
        for f in files:
            try:
                rel = str(f.relative_to(project_root))
            except ValueError:
                rel = str(f)
            hash_cache[rel] = _get_file_hash(f)
        _write_hash_cache(project_root, hash_cache)
        elapsed = time.time() - start_time
        langs_used = set()
        for f in files:
            ext = f.suffix.lower()
            lang = SOURCE_EXTENSIONS.get(ext, "unknown")
            langs_used.add(lang)

        stats = {
            "total_files": len(files),
            "total_chunks": index.num_docs,
            "languages": sorted(langs_used),
            "force_rebuild": True,
        }
        _save_meta(project_root, stats)

        return (
            f"✅ 全量索引完成！\n"
            f"   📁 已索引 {len(files)} 个文件\n"
            f"   🧩 共 {index.num_docs} 个代码块\n"
            f"   🌐 覆盖语言: {', '.join(sorted(langs_used))}\n"
            f"   ⏱️  耗时: {elapsed:.2f}s\n"
            f"   📍 索引位置: {RAG_INDEX_DIR}/"
        )

    # 增量更新
    old_hashes = _read_hash_cache(project_root)
    new_hashes: dict[str, str] = {}
    changed_files: list[Path] = []
    unchanged_count = 0

    for f in files:
        try:
            rel = str(f.relative_to(project_root))
        except ValueError:
            rel = str(f)
        h = _get_file_hash(f)
        new_hashes[rel] = h
        if rel not in old_hashes or old_hashes[rel] != h:
            changed_files.append(f)
        else:
            unchanged_count += 1

    # 删除已不存在的文件
    deleted_files = [k for k in old_hashes if k not in new_hashes]

    if not changed_files and not deleted_files:
        _write_hash_cache(project_root, new_hashes)
        elapsed = time.time() - start_time
        meta = _load_meta(project_root)
        return (
            f"✅ 索引已是最新（{unchanged_count} 个文件未变更）\n"
            f"   📁 共 {meta.get('total_files', 0)} 个文件已索引\n"
            f"   🧩 共 {meta.get('total_chunks', 0)} 个代码块\n"
            f"   ⏱️  检查耗时: {elapsed:.2f}s"
        )

    # 加载现有索引，更新变更的文件
    existing_index = _load_index(project_root)
    if existing_index is None:
        # 没有现有索引，全量构建
        return index_project(path, force=True)

    # 移除已删除文件的块
    if deleted_files:
        existing_index.documents = [
            d for d in existing_index.documents
            if d.file_path not in deleted_files
        ]
        # 重建倒排索引
        existing_index = _rebuild_from_documents(existing_index.documents)
        existing_index.num_docs = len(existing_index.documents)

    # 重新索引变更的文件
    new_chunks: list[CodeChunk] = []
    for f in changed_files:
        try:
            rel = str(f.relative_to(project_root))
        except ValueError:
            rel = str(f)
        ext = f.suffix.lower()
        lang = SOURCE_EXTENSIONS.get(ext, "unknown")
        chunks = _chunk_file(f, lang)
        for c in chunks:
            c.file_path = rel
            new_chunks.append(c)

    # 移除变更文件的旧块
    changed_paths = set()
    for f in changed_files:
        try:
            changed_paths.add(str(f.relative_to(project_root)))
        except ValueError:
            changed_paths.add(str(f))

    existing_index.documents = [
        d for d in existing_index.documents
        if d.file_path not in changed_paths
    ]

    # 添加新块
    for c in new_chunks:
        existing_index.add_document(c)

    existing_index.num_docs = len(existing_index.documents)
    # 重建倒排索引
    existing_index = _rebuild_from_documents(existing_index.documents)

    _save_index(project_root, existing_index)
    _write_hash_cache(project_root, new_hashes)

    elapsed = time.time() - start_time
    langs_used = set()
    for f in files:
        ext = f.suffix.lower()
        lang = SOURCE_EXTENSIONS.get(ext, "unknown")
        langs_used.add(lang)

    stats = {
        "total_files": len(files),
        "total_chunks": existing_index.num_docs,
        "languages": sorted(langs_used),
        "force_rebuild": False,
    }
    _save_meta(project_root, stats)

    return (
        f"✅ 增量索引完成！\n"
        f"   📁 {len(changed_files)} 个文件已更新"
        + (f"，{len(deleted_files)} 个已移除" if deleted_files else "")
        + f"\n"
        f"   🧩 共 {existing_index.num_docs} 个代码块\n"
        f"   ⏱️  耗时: {elapsed:.2f}s"
    )


def _rebuild_from_documents(documents: list[CodeChunk]) -> TfIdfIndex:
    """从文档列表重建倒排索引。"""
    index = TfIdfIndex()
    for doc in documents:
        index.add_document(doc)
    return index


def query_index(query: str, top_k: int = DEFAULT_TOP_K, path: str | None = None) -> str:
    """搜索已索引的代码库。

    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数
        path: 项目路径

    Returns:
        格式化的搜索结果
    """
    project_root = _find_project_root(path)
    index = _load_index(project_root)

    if index is None:
        return "⚠️ 尚未建立索引。请先运行 rag_index() 建立索引。"

    keywords = _extract_keywords_from_query(query)
    if not keywords:
        # 如果没提取到关键词，搜索完整查询
        keywords = [w.lower() for w in re.findall(r'\b\w+\b', query) if len(w) >= 2]

    if not keywords:
        return "⚠️ 无法从查询中提取有效的搜索词。"

    results = index.search(keywords, top_k=top_k)

    if not results:
        return f"🔍 未找到与「{query}」相关的结果。"

    # 格式化输出
    lines: list[str] = [
        f"🔍 搜索: «{query}»",
        f"📊 匹配 {len(results)} 个结果:\n",
    ]

    for rank, (doc_id, score) in enumerate(results, 1):
        chunk = index.documents[doc_id]
        # 截断内容显示
        content_preview = chunk.content[:300]
        if len(chunk.content) > 300:
            content_preview += "..."

        lines.append(f"─── [{rank}] 匹配度 {score:.2f} ───")
        lines.append(f"📁 {chunk.file_path}:{chunk.start_line}-{chunk.end_line}")
        lines.append(f"🏷️  {chunk.chunk_type}: {chunk.name}")
        lines.append(f"💬 {content_preview}")
        lines.append("")

    return "\n".join(lines)


def get_index_status(path: str | None = None) -> str:
    """查看索引状态。"""
    project_root = _find_project_root(path)
    meta = _load_meta(project_root)
    index = _load_index(project_root)

    if index is None:
        return "📭 RAG 索引状态：未建立\n\n请运行 `rag_index()` 建立索引。"

    updated = meta.get("updated_at", 0)
    if updated:
        updated_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated))
    else:
        updated_str = "未知"

    # 统计语言分布
    lang_count: Counter = Counter()
    for doc in index.documents:
        lang_count[doc.language] += 1
    lang_stats = "\n".join(
        f"    {lang}: {count} 个块"
        for lang, count in lang_count.most_common()
    )

    return (
        f"📊 RAG 索引状态\n"
        f"   ─────────────────\n"
        f"   📁 文件数: {meta.get('total_files', '?')}\n"
        f"   🧩 代码块: {index.num_docs}\n"
        f"   🌐 语言: {', '.join(meta.get('languages', []))}\n"
        f"   ⏱️  上次更新: {updated_str}\n"
        f"\n"
        f"   📦 语言分布:\n"
        f"{lang_stats}"
    )


# ═══════════════════════════════════════════════════════════════
# 自动检索（供 llm.py / main.py 注入使用）
# ═══════════════════════════════════════════════════════════════


def auto_retrieve(query: str, path: str | None = None) -> str | None:
    """自动检索相关代码上下文。供对话系统注入 system prompt 使用。

    Args:
        query: 用户输入或当前对话上下文
        path: 项目路径

    Returns:
        格式化的代码上下文文本，供注入到 system prompt；无匹配则返回 None
    """
    project_root = _find_project_root(path)
    index = _load_index(project_root)

    if index is None:
        return None

    keywords = _extract_keywords_from_query(query)
    if not keywords:
        keywords = [w.lower() for w in re.findall(r'\b\w+\b', query) if len(w) >= 2]
    if not keywords:
        return None

    results = index.search(keywords, top_k=AUTO_RETRIEVE_TOP_K)
    if not results:
        return None

    # 构建注入文本（不超过 AUTO_RETRIEVE_MAX_CHARS）
    parts: list[str] = [
        "📖 以下是项目中与当前问题相关的代码上下文（自动检索）：",
        "",
    ]
    total_chars = len(parts[0])

    for doc_id, score in results:
        chunk = index.documents[doc_id]
        header = f"📁 {chunk.file_path}:{chunk.start_line} ({chunk.chunk_type}: {chunk.name})"

        # 截断内容
        content = chunk.content
        max_content = AUTO_RETRIEVE_MAX_CHARS // max(len(results), 1) - len(header) - 20
        if max_content < 100:
            max_content = 100
        if len(content) > max_content:
            content = content[:max_content] + "\n    ...（截断）"

        entry = f"\n{header}\n{content}\n"
        if total_chars + len(entry) > AUTO_RETRIEVE_MAX_CHARS + 200:
            break

        parts.append(entry)
        total_chars += len(entry)

    if len(parts) <= 1:
        return None

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# execute（工具调度入口）
# ═══════════════════════════════════════════════════════════════


def execute(name: str, args: dict) -> str:
    """工具调度入口。"""
    if name == "rag_index":
        return index_project(
            path=args.get("path"),
            force=args.get("force", False),
        )
    elif name == "rag_query":
        return query_index(
            query=args["query"],
            top_k=args.get("top_k", DEFAULT_TOP_K),
        )
    elif name == "rag_status":
        return get_index_status()
    else:
        raise ValueError(f"未知的 RAG 工具: {name}")
