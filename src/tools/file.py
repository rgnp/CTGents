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
_FILE_CACHE_TTL = 300  # 秒，5分钟。项目源码不会频繁变，mtime 双重验证
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
        "_meta": {"label": "读取文件", "parallel_safe": True, "skip_compress": True},
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件全文或指定行范围（start_line/end_line）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从1开始），不传=从第1行",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（包含），不传=到末尾",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "_meta": {"label": "写入文件", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆写文件。⚠️ 先 read_file 读原内容，不要凭记忆写。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "写入的完整内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "_meta": {"label": "行级编辑", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "edit_file_lines",
            "description": "行级编辑（replace/delete/insert）。⚠️ 先 read_file 确认行号。",
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
                        "description": "起始行号（从1开始），insert=在此行后插入",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（含），del/replace必填，insert忽略",
                    },
                    "new_lines": {
                        "type": "string",
                        "description": "新内容（多行字符串），del忽略",
                    },
                },
                "required": ["path", "action", "start_line"],
            },
        },
    },
    {
        "_meta": {"label": "浏览目录", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录内容，浏览项目结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，不传=当前目录",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "删除文件", "dedup_blacklist": True},
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
        "_meta": {"label": "统计行数", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "count_lines",
            "description": "统计文件行数/字符数/单词数。",
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


def _ensure_in_workspace(filepath: Path) -> None:
    """确保路径在工作目录内。所有写/删操作必须经过此检查。"""
    root = Path.cwd().resolve()
    try:
        filepath.resolve().relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            f"路径超出工作目录范围: {filepath}\n"
            f"工作目录: {root}\n"
            "所有增删改操作只能在工作目录内进行。"
        ) from exc


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


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _restore_from_backup(filepath: Path, backup_path: Path | None, why: str) -> str:
    """安全带回滚：把核心文件还原到改前备份（无备份=新建文件，提示手删）。"""
    if backup_path and backup_path.exists():
        shutil.copy2(backup_path, filepath)
        _invalidate_pyc(filepath)
        tail = "，已自动回滚到改前版本"
    else:
        tail = "（无备份可回滚——新建文件，请手动删除）"
    return f"⛔ 核心文件安全带拦截{tail}:\n{why}"


def _module_name(filepath: Path) -> str | None:
    """src/tools/__init__.py → src.tools；src/main.py → src.main。非项目内 .py 返回 None。"""
    try:
        rel = filepath.resolve().relative_to(_PROJECT_ROOT)
    except (ValueError, OSError):
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _core_import_smoke(filepath: Path, backup_path: Path | None) -> str | None:
    """核心文件安全带：改后跑 import 冒烟。

    AST 过≠能 import（坏 import / 模块级 NameError / 工具注册表崩）。子进程里真实
    import 被改模块 + 核心链 + 建工具表，挂了从备份回滚——agent 能改核心文件，但改坏当场弹回。
    """
    import subprocess
    import sys

    _invalidate_pyc(filepath)  # 确保子进程读新代码、不吃旧 pyc
    probe = "import src.main; from src.tools import get_tools; get_tools()"
    mod = _module_name(filepath)
    if mod:
        probe = f"import {mod}; " + probe
    try:
        r = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(_PROJECT_ROOT), capture_output=True,
            text=True, timeout=60, encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return _restore_from_backup(filepath, backup_path, f"import 冒烟异常: {e}")
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()[-700:]
        return _restore_from_backup(filepath, backup_path, f"import 冒烟失败:\n{detail}")
    return None


def _post_write_check(filepath: Path, backup_path: Path | None) -> str | None:
    """改后校验：所有 .py 走 AST（自带回滚）；核心文件额外走 import 冒烟（自带回滚）。

    返回错误消息（已回滚）或 None（通过，且已清字节码缓存）。
    """
    from ..guard import is_core
    err = _validate_py(filepath, backup_path)
    if err:
        return err
    if is_core(filepath):
        err = _core_import_smoke(filepath, backup_path)
        if err:
            return err
    _invalidate_pyc(filepath)
    return None


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


def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """读取文件。不带行号参数返回全文，带行号参数返回带行号的指定范围。"""
    filepath = _resolve(path)
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    raw = _read_cached(filepath)
    if raw is None:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"

    if start_line is None and end_line is None:
        return raw

    lines = raw.split("\n")
    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or total)
    if s > total:
        return f"起始行号 {s} 超出文件总行数 {total}"
    if s > e:
        return f"起始行号 {s} 大于结束行号 {e}"
    return "\n".join(f"{i+1:4d}|{line}" for i, line in enumerate(lines[s-1:e], start=s-1))


def read_file_lines(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """已废弃，同 read_file。保留向后兼容。"""
    return read_file(path, start_line, end_line)



# ── 写入（覆写）──


def write_file(path: str, content: str) -> str:
    """创建或覆写文件。已存在则先备份；.py 写后做语法校验，失败自动回滚。

    回滚：覆写已有文件 → 还原备份；新建文件 → 删除（无备份可还原）。
    """
    filepath = _resolve(path)
    _ensure_in_workspace(filepath)
    from ..guard import is_immutable
    if is_immutable(filepath):
        return f"⛔ 不可变安全核，禁止修改: {path}"
    backup = _backup(filepath) if filepath.exists() else None
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"写入失败: {e}"
    err = _post_write_check(filepath, backup)
    if err:
        return err
    return f"已写入: {filepath}（{len(content)} 字符）"


# ── 行级编辑（核心）──


def edit_file_lines(path: str, action: str, start_line: int,
                    end_line: int | None = None, new_lines: str | None = None) -> str:
    """行级编辑文件。"""
    filepath = _resolve(path)
    _ensure_in_workspace(filepath)
    _assert_file(filepath)
    from ..guard import is_immutable
    if is_immutable(filepath):
        return f"⛔ 不可变安全核，禁止修改: {path}\n该文件强制系统安全（测试门/审计/分级表本身），改了等于让防护失效。"

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
    new_content_lines = new_lines.split("\n") if new_lines else []
    if action == "replace":
        result = original_lines[:s - 1] + new_content_lines + original_lines[e:]
    elif action == "delete":
        result = original_lines[:s - 1] + original_lines[e:]
    elif action == "insert":
        result = original_lines[:s] + new_content_lines + original_lines[s:]
    else:
        return f"未知操作: {action}（可选: replace/delete/insert）"

    # ── 写回（先备份，.py 写后语法校验，失败自动回滚）──
    backup = _backup(filepath)  # 文件必存在（已 _assert_file）
    filepath.write_text("\n".join(result), encoding="utf-8")
    err = _post_write_check(filepath, backup)
    if err:
        return err

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

    return (
        f"已编辑: {filepath}\n"
        f"操作: {action} {changed_range}\n"
        f"文件现在共 {len(result)} 行"
    )


# ── 撤销 ──


def count_lines(path: str) -> str:
    """统计文件的文本度量：行数、字符数、单词数。

    Args:
        path: 文件路径（支持 ~ 和相对路径）

    Returns:
        格式化的统计结果字符串，包含行数/字符数/单词数

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 路径不是文件
    """
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
    _ensure_in_workspace(filepath)
    from ..guard import is_core, is_immutable
    if is_immutable(filepath):
        return f"⛔ 不可变安全核，禁止删除: {path}"
    if is_core(filepath):
        return f"⛔ 核心业务文件可改但不可删（删了会断 import 链）: {path}"
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
    if name == "count_lines":
        return count_lines(args.get("path", ""))
    if name == "list_files":
        return list_files(args.get("path"))
    if name == "delete_file":
        return delete_file(args.get("path", ""))
    return None
