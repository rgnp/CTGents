"""文件操作工具：读写、行级编辑、备份与撤销。"""

import contextlib
import shutil
import time
from datetime import datetime
from pathlib import Path

# ── list_files 缓存 ──
_LIST_CACHE_TTL = 300   # 秒（同 web 工具一致，5 分钟）
_list_cache: dict[str, tuple[float, str]] = {}
# ── 文件内容缓存 ──（read_file / read_file_lines 共用）
_FILE_CACHE_TTL = 60    # 秒，短 TTL 避免读取过期内容
_FILE_CACHE_MAX = 50    # 最大条目数
_file_cache: dict[str, tuple[float, float, str]] = {}  # key → (ts, mtime, content)
# ── 备份目录 ──
# ── 备份目录 ──
BACKUP_DIR = Path.home() / ".agent_backups"


def _backup_path(filepath: Path) -> Path:
    """生成备份路径：~/.agent_backups/<相对路径>/<时间戳>_<文件名>"""
    # 尝试用工作目录做相对路径
    try:
        rel = filepath.relative_to(Path.cwd())
    except ValueError:
        rel = filepath.name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / str(rel).replace("\\", "/") / f"{ts}_{filepath.name}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


# ── 变更追踪 ──

# 项目文档文件（搜索连带影响时的目标文件）
# ── 变更追踪 ──

# 搜索范围：所有文档类和配置类文件（不硬编码清单，自动发现）
_SEARCH_EXTENSIONS = {
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env.example",
}

# 排除的目录
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", ".eggs", "dist", "build", ".next", "target",
    "bin", "obj", "vendor", ".agent_backups",
    "sessions", "memory", "plugins",
}

# 最大搜索文件数（防止大项目卡死）
_MAX_SEARCH_FILES = 200
# 最大结果数
_MAX_RESULTS = 20
# 跳过超过此大小的文件（1MB）
_MAX_FILE_SIZE = 1024 * 1024


def _get_changed_files() -> list[str]:
    """通过 git 获取当前工作区的变更文件列表。"""
    import subprocess

    changed: set[str] = set()

    try:
        r1 = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=5, cwd=Path.cwd(),
        )
        if r1.returncode == 0:
            for line in r1.stdout.strip().split("\n"):
                if line.strip():
                    changed.add(line.strip())

        r2 = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=5, cwd=Path.cwd(),
        )
        if r2.returncode == 0:
            for line in r2.stdout.strip().split("\n"):
                if line.strip():
                    changed.add(line.strip())

        r3 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=5, cwd=Path.cwd(),
        )
        if r3.returncode == 0:
            for line in r3.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("?? "):
                    changed.add(line[3:].strip())
    except Exception:
        pass

    return sorted(changed)


def _find_affected_files(changed_file: str) -> list[str]:
    """在项目所有文档/配置文件中搜索提到变更文件名的文件。

    不依赖硬编码清单，全项目自动发现，绝对不漏。
    返回匹配的文件路径列表（相对路径）。
    """
    root = Path.cwd()
    if not root.exists():
        return []

    filename = Path(changed_file).name
    patterns = [filename, changed_file.replace("\\", "/")]

    affected: list[str] = []
    scanned = 0

    for f in root.rglob("*"):
        if not f.is_file():
            continue

        # 跳过自身
        rel = f.relative_to(root)
        if str(rel) == changed_file:
            continue

        # 跳过排除目录下的文件
        parts = rel.parts
        if any(p in _EXCLUDE_DIRS or p.startswith(".") for p in parts):
            continue

        # 只搜索文档/配置类文件
        if f.suffix not in _SEARCH_EXTENSIONS and f.name not in ("Dockerfile", "Makefile"):
            continue

        # 跳过大文件
        try:
            if f.stat().st_size > _MAX_FILE_SIZE:
                continue
        except OSError:
            continue

        scanned += 1
        if scanned > _MAX_SEARCH_FILES:
            break

        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                if pattern in text:
                    affected.append(str(rel))
                    break
        except Exception:
            continue

    return affected[:_MAX_RESULTS]


def _track_changes(just_modified: str) -> str:
    """变更追踪：分析刚刚修改的文件，找出项目中还需要同步更新的文件。

    核心逻辑：
      改文件 → git diff 拿变更列表 → 全项目 grep 搜哪些文件提到了这些变更 → 汇总提醒

    优点：不维护依赖清单、不记"谁依赖谁"、每次实时分析、绝对不漏。
    """
    changed_all = _get_changed_files()
    if not changed_all:
        return ""

    # 收集所有受影响的文件
    all_affected: set[str] = set()
    for f in changed_all:
        for affected in _find_affected_files(f):
            all_affected.add(affected)

    # 排除自身
    just_name = Path(just_modified).name
    all_affected = {a for a in all_affected if Path(a).name != just_name}
    # 排除不存在的文件
    all_affected = {a for a in all_affected if (Path.cwd() / a).exists()}

    if not all_affected:
        return ""

    docs_list = "、".join(sorted(all_affected)[:_MAX_RESULTS])
    total = len(all_affected)
    suffix = f" 等 {total} 个文件" if total > _MAX_RESULTS else ""
    return (
        f"\n\n📋 变更追踪：本轮共修改 {len(changed_all)} 个文件\n"
        f"  上述文件中提到的其他文件{docs_list}{suffix}\n"
        f"  提示：这些文件可能也需要同步更新。"
    )





TOOLS_FILE = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取本地文件内容。可以读取项目中的代码、文档、配置等文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持相对路径或绝对路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_lines",
            "description": (
                "带行号读取文件。每行前面标注行号，适合定位要修改的行。"
                "修改前先用此工具查看文件内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始），不传则从第 1 行",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（包含），不传则到文件末尾",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆写本地文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持相对路径或绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file_lines",
            "description": "行级编辑文件：替换、删除或插入指定行。行号从 1 开始。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "action": {
                        "type": "string",
                        "enum": ["replace", "delete", "insert"],
                        "description": "操作类型",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始）。insert 时在此行之后插入",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（包含）。delete/replace 时必填，insert 时忽略",
                    },
                    "new_lines": {
                        "type": "string",
                        "description": "新内容（多行字符串）。replace/insert 时必填，delete 时忽略",
                    },
                },
                "required": ["path", "action", "start_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_edit",
            "description": "撤销最近一次对该文件的编辑。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要撤销的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录中的文件和子目录。用于浏览项目结构、查找文件、了解目录布局。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，支持相对路径或绝对路径。不传则列出当前目录。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除指定文件。不可恢复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_lines",
            "description": "统计文件的行数、字符数和单词数。快速了解文件大小。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


# ── 辅助函数 ──


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _assert_file(filepath: Path) -> None:
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    if not filepath.is_file():
        raise ValueError(f"路径不是文件: {filepath}")


def _backup(filepath: Path) -> Path:
    """备份文件，返回备份路径。"""
    dst = _backup_path(filepath)
    shutil.copy2(filepath, dst)
    return dst


def _validate_py(filepath: Path, backup_path: Path | None) -> str | None:
    """Python 语法校验。失败则自动回滚到备份，返回错误消息。"""
    if filepath.suffix != ".py":
        return None
    try:
        compile(filepath.read_text(encoding="utf-8"), str(filepath), "exec")
        return None
    except SyntaxError as e:
        if backup_path and backup_path.exists():
            shutil.copy2(backup_path, filepath)
        else:
            with contextlib.suppress(OSError):
                filepath.unlink(missing_ok=True)
        return (
            f"语法校验失败，已自动回滚:\n"
            f"  {e.filename}:{e.lineno}:{e.offset} {e.msg}\n"
            f"  {e.text.rstrip() if e.text else ''}\n"
            f"  请修正后重试。"
        )


def _validate_imports(filepath: Path, backup_path: Path | None) -> str | None:
    """Import 校验：语法正确不代表 import 不报错。

    用子进程隔离执行 import——捕获 ImportError/ModuleNotFoundError。
    这类错误在用户重启时会直接崩溃，必须在编辑阶段拦截。
    """
    import os
    import subprocess

    if filepath.suffix != ".py":
        return None

    # 从文件路径推断 Python 模块名
    # D:/project/agent/src/tools/git.py      → src.tools.git
    # D:/project/agent/src/tools/__init__.py → src.tools
    # D:/project/agent/src/llm.py            → src.llm
    parts = list(filepath.parts)
    try:
        src_idx = parts.index("src")
    except ValueError:
        return None  # 不在 src 包内，跳过

    module_parts = list(parts[src_idx:])
    module_parts[-1] = module_parts[-1].replace(".py", "")
    if module_parts[-1] == "__init__":
        module_parts.pop()
    module_name = ".".join(module_parts)

    # 项目根是 src 的父目录
    project_root = Path(*parts[:src_idx])

    try:
        r = subprocess.run(
            ["py", "-c", f"import {module_name}"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            cwd=str(project_root),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if r.returncode != 0:
            stderr = r.stderr.strip()
            # 只拦截真正的 import 错误，不拦截运行时初始化错误（如缺失 .env）
            if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
                if backup_path and backup_path.exists():
                    shutil.copy2(backup_path, filepath)
                else:
                    # 新文件无备份，直接删除
                    with contextlib.suppress(OSError):
                        filepath.unlink(missing_ok=True)
                return (
                    f"导入校验失败——语法正确但 import 会崩溃，已自动回滚！\n"
                    f"  模块: {module_name}\n"
                    f"  {stderr[:600]}\n"
                    f"  请修正后重试。"
                )
        return None
    except Exception:
        return None  # 子进程异常不阻塞编辑


def _invalidate_pyc(filepath: Path) -> None:
    """删除 .py 文件对应的 __pycache__ 中的字节码缓存。

    编辑后必须清缓存，否则 Python 可能用旧的 .pyc，
    导致 `from src.llm import xx` 拿到旧版本代码。
    """
    if filepath.suffix != ".py":
        return
    import os

    pycache = filepath.parent / "__pycache__"
    if not pycache.is_dir():
        return
    # 匹配规则：module_name.cpython-*.pyc
    stem = filepath.stem
    for pyc in pycache.glob(f"{stem}.cpython-*.pyc"):
        with contextlib.suppress(OSError):
            os.remove(pyc)
    # 也删除 .pyo（老版本 Python）
    for pyc in pycache.glob(f"{stem}.*.pyo"):
        with contextlib.suppress(OSError):
            os.remove(pyc)

def _list_backups(filepath: Path) -> list[Path]:
    """列出文件的所有备份，按时间倒序。"""
    try:
        rel = filepath.relative_to(Path.cwd())
    except ValueError:
        rel = filepath.name
    backup_dir = BACKUP_DIR / str(rel).replace("\\", "/")
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.iterdir(), reverse=True)


# ── 读取 ──


def _read_cached(path: Path) -> str:
    """从缓存读取文件内容，未命中或过期则重新读取。基于 mtime 验证。"""
    key = str(path.resolve())
    meta = _file_cache.get(key)
    now = time.time()
    if meta is not None:
        ts, cached_mtime, content = meta
        try:
            actual_mtime = path.stat().st_mtime
        except OSError:
            actual_mtime = 0
        if (now - ts) < _FILE_CACHE_TTL and cached_mtime == actual_mtime:
            return content
    # 未命中或过期：重新读取
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # 标记二进制文件
    mtime = path.stat().st_mtime
    _file_cache[key] = (now, mtime, raw)
    # 上限淘汰
    if len(_file_cache) > _FILE_CACHE_MAX:
        oldest = min(_file_cache.keys(), key=lambda k: _file_cache[k][0])
        del _file_cache[oldest]
    return raw


def read_file(path: str) -> str:
    """读取本地文件内容。"""
    filepath = _resolve(path)
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    raw = _read_cached(filepath)
    if raw is None:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"
    return raw


def read_file_lines(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """带行号读取文件指定范围。"""
    filepath = _resolve(path)
    _assert_file(filepath)

    raw = _read_cached(filepath)
    if raw is None:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"
    lines = raw.split("\n")

    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or total)

    if s > total:
        return f"起始行号 {s} 超出文件总行数 {total}"
    if s > e:
        return f"起始行号 {s} 大于结束行号 {e}"

    result_lines = []
    for i in range(s - 1, e):
        line_num = i + 1
        # 行号右对齐 + 竖线，便于阅读
        result_lines.append(f"{line_num:>6} | {lines[i]}")

    info = f"文件: {filepath}（共 {total} 行），显示第 {s}-{e} 行"
    return info + "\n" + "-" * len(info) + "\n" + "\n".join(result_lines)


# ── 写入（覆写）──


def write_file(path: str, content: str) -> str:
    """创建或覆写文件。写入前自动备份，.py 文件自动语法校验。"""
    filepath = _resolve(path)
    from ..guard import is_protected
    if is_protected(filepath):
        return f"⛔ 受保护文件，禁止修改: {path}\n该文件是系统自愈模块，修改它可能导致系统无法自动恢复。"
    backup = _backup(filepath) if filepath.exists() else None
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        err = _validate_py(filepath, backup)
        if err:
            return f"写入失败: {err}"
        err = _validate_imports(filepath, backup)
        if err:
            return f"写入失败: {err}"
        _invalidate_pyc(filepath)
        track = _track_changes(str(filepath))
        return f"已写入: {filepath}（{len(content)} 字符）{track}"
    except OSError as e:
        return f"写入失败: {e}"


# ── 行级编辑（核心）──


def edit_file_lines(path: str, action: str, start_line: int,
                    end_line: int | None = None, new_lines: str | None = None) -> str:
    """行级编辑文件。"""
    filepath = _resolve(path)
    _assert_file(filepath)
    from ..guard import is_protected
    if is_protected(filepath):
        return f"⛔ 受保护文件，禁止修改: {path}\n该文件是系统自愈模块，修改它可能导致系统无法自动恢复。"

    # 读取原文件
    try:
        original_lines = filepath.read_text(encoding="utf-8").split("\n")
    except UnicodeDecodeError:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"

    total = len(original_lines)
    s = start_line  # 从 1 开始

    # ── 参数校验 ──
    if s < 1 or s > total:
        return f"行号 {s} 超出范围（文件共 {total} 行，从 1 开始）"

    if action in ("replace", "delete"):
        e = end_line
        if e is None:
            return "replace/delete 操作需要指定 end_line"
        if e < s or e > total:
            return f"结束行号 {e} 超出范围（文件共 {total} 行）"

    if action in ("replace", "insert") and not new_lines:
        return f"{action} 操作需要提供 new_lines"

    # ── 执行操作 ──
    backup_path = _backup(filepath)
    new_content_lines = new_lines.split("\n") if new_lines else []

    if action == "replace":
        result = original_lines[:s - 1] + new_content_lines + original_lines[e:]
    elif action == "delete":
        result = original_lines[:s - 1] + original_lines[e:]
    elif action == "insert":
        result = original_lines[:s] + new_content_lines + original_lines[s:]
    else:
        return f"未知操作: {action}（可选: replace/delete/insert）"

    # ── 写回 ──
    filepath.write_text("\n".join(result), encoding="utf-8")

    # ── Python 语法校验 ──
    err = _validate_py(filepath, backup_path)
    if err:
        track = _track_changes(str(filepath))
        return f"编辑失败: {err}{track}"

    # ── Import 校验 ──
    err = _validate_imports(filepath, backup_path)
    if err:
        track = _track_changes(str(filepath))
        return f"编辑失败: {err}{track}"

    # ── 清理字节码缓存 ──
    _invalidate_pyc(filepath)

    # ── 构造详细的变更报告 ──
    old_count = (e - s + 1) if action in ("replace", "delete") else 0
    new_count = len(new_content_lines)
    delta = new_count - old_count

    changed_range = f"第 {s} 行"
    if action == "replace":
        changed_range += f"~第 {e} 行（{old_count} 行 → {new_count} 行，{'+' if delta > 0 else ''}{delta} 行）"
    elif action == "delete":
        changed_range += f"~第 {e} 行（删除 {old_count} 行）"
    elif action == "insert":
        changed_range += f"后（插入 {new_count} 行）"

    track = _track_changes(str(filepath))
    return (
        f"已编辑: {filepath}\n"
        f"操作: {action} {changed_range}\n"
        f"备份: {backup_path}\n"
        f"文件现在共 {len(result)} 行"
        f"{track}"
    )


# ── 撤销 ──


def undo_edit(path: str) -> str:
    """撤销最近一次编辑。"""
    filepath = _resolve(path)
    backups = _list_backups(filepath)
    if not backups:
        return f"没有找到 {path} 的备份记录"

    latest = backups[0]
    if not latest.exists():
        return f"备份文件已损坏: {latest}"

    # 当前文件也备份一次（防误操作）
    if filepath.exists():
        _backup(filepath)

    shutil.copy2(latest, filepath)
    return f"已撤销: {filepath}（恢复自 {latest.name}）"

def count_lines(path: str) -> str:
    """统计文件行数、字符数、单词数。"""
    filepath = _resolve(path)
    _assert_file(filepath)
    try:
        text = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"

    lines = text.split("\n")
    line_count = len(lines)
    char_count = len(text)
    word_count = len(text.split())

    return (
        f"文件: {filepath}\n"
        f"  行数: {line_count}\n"
        f"  字符: {char_count}\n"
        f"  单词: {word_count}"
    )




# ── 其他已有工具 ──


def list_files(path: str | None) -> str:
    """列出目录内容（带 3 秒 TTL 缓存，避免同轮重复 IO）。"""
    dirpath = _resolve(path) if path else Path.cwd()

    # ── TTL 缓存 ──
    now = time.time()
    key = str(dirpath)
    if key in _list_cache:
        ts, cached = _list_cache[key]
        if now - ts < _LIST_CACHE_TTL:
            return cached

    if not dirpath.exists():
        return f"目录不存在: {path or '.'}"
    if not dirpath.is_dir():
        return f"路径不是目录: {path or '.'}"
    try:
        entries = sorted(dirpath.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return f"没有权限访问: {dirpath}"

    lines = [f"{dirpath}（{len(entries)} 项）\n"]
    for entry in entries:
        kind = "/" if entry.is_dir() else ""
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0

        if size >= 1024 * 1024:
            size_str = f"  [{size // (1024 * 1024)}M]"
        elif size >= 1024:
            size_str = f"  [{size // 1024}K]"
        else:
            size_str = ""

        lines.append(f"  {entry.name}{kind}{size_str}")

    result = "\n".join(lines)
    _list_cache[key] = (now, result)
    return result
def delete_file(path: str) -> str:
    """删除文件。删除前会告知用户。不可恢复。"""
    filepath = _resolve(path)
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    try:
        filepath.unlink()
        return f"已删除: {filepath}"
    except OSError as e:
        return f"删除失败: {e}"


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "read_file":
        return read_file(args.get("path", ""))
    if name == "read_file_lines":
        return read_file_lines(
            args.get("path", ""),
            args.get("start_line"),
            args.get("end_line"),
        )
    if name == "write_file":
        return write_file(args.get("path", ""), args.get("content", ""))
    if name == "edit_file_lines":
        action = args.get("action", "")
        if action not in ("replace", "delete", "insert"):
            return f"edit_file_lines 缺少有效的 action 参数（received: {action!r}），必须为 replace/delete/insert 之一"
        return edit_file_lines(
            args.get("path", ""),
            action,
            args.get("start_line", 1),
            args.get("end_line"),
            args.get("new_lines"),
        )
    if name == "undo_edit":
        return undo_edit(args.get("path", ""))
    if name == "count_lines":
        return count_lines(args.get("path", ""))
    if name == "list_files":
        return list_files(args.get("path"))
    if name == "delete_file":
        return delete_file(args.get("path", ""))
    return None
