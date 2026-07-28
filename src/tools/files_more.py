"""基础文件工具补充：移动/复制/按名查找/建目录。用 Toolkit 统一定义（样板最小化）。

写操作复用 file.py 的工作目录边界 + guard 不可变核检查（与 write/delete 同一道闸）。
错误一律 raise → Toolkit.execute 统一包成 {"error": ...}（标准化 observation）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..guard import is_core, is_immutable
from ._toolkit import Toolkit
from .file import (
    _EXCLUDE_DIRS,
    _backup,
    _ensure_in_workspace,
    _post_write_check,
    _resolve,
)

tk = Toolkit()

# 按名查找的结果上限（结构性常量，留本模块）
_FIND_MAX = 100


def _guard_write(target: Path) -> None:
    """写/移动目标必经的闸：工作目录内 + 非不可变安全核。"""
    _ensure_in_workspace(target)
    if is_immutable(target):
        raise PermissionError(f"不可变安全核，禁止操作: {target}")


@tk.tool(
    label="移动文件",
    group="core",
    params={"src": "现有文件路径", "dst": "目标路径（含新文件名即重命名）"},
    dedup_blacklist=True,
)
def move_file(src: str, dst: str) -> str:
    """移动或重命名文件。"""
    s, d = _resolve(src), _resolve(dst)
    if not s.exists() or not s.is_file():
        raise FileNotFoundError(f"源文件不存在或不是文件: {src}")
    _ensure_in_workspace(s)
    if is_immutable(s) or is_core(s):
        raise PermissionError(f"不可变/核心文件不可移动: {src}")
    _guard_write(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"已移动: {s} → {d}"


@tk.tool(
    label="按名查找",
    group="core",
    params={"pattern": "文件名 glob，如 *.py / test_*.py", "path": "搜索根目录，不传=当前目录"},
    parallel_safe=True,
)
def find_files(pattern: str, path: str | None = None) -> str:
    """按文件名 glob 递归查找文件（grep_code 搜内容，这个搜名字）。"""
    root = _resolve(path) if path else Path.cwd()
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(f"目录不存在: {path or '.'}")
    hits: list[str] = []
    for f in root.rglob(pattern):
        rel = f.relative_to(root) if f.is_relative_to(root) else f
        if any(part in _EXCLUDE_DIRS or part.startswith(".") for part in rel.parts):
            continue
        hits.append(str(rel))
        if len(hits) >= _FIND_MAX:
            break
    if not hits:
        return f"没有匹配 {pattern!r} 的文件（根目录 {root}）"
    head = "\n".join(f"  {h}" for h in sorted(hits))
    cap = f"（已达上限 {_FIND_MAX}）" if len(hits) >= _FIND_MAX else ""
    return f"匹配 {pattern!r} 的文件（{len(hits)} 个{cap}）：\n{head}"


@tk.tool(
    label="替换编辑",
    group="core",
    params={
        "path": "文件路径",
        "old": "要替换的原文，逐字精确匹配（含空格/缩进/换行），默认须唯一",
        "new": "替换成的新文本",
        "replace_all": "替换全部匹配（默认 false，只换唯一一处）",
    },
    dedup_blacklist=True,
)
def replace_in_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """按字符串精确匹配编辑文件，免行号漂移（优先于 edit_file_lines）。

    old 必须逐字匹配现有内容（先 read_file 拷过来最稳）。默认要求唯一匹配——多处
    匹配会报错让你加上下文区分，避免误改；确需全改传 replace_all=true。
    """
    fp = _resolve(path)
    _ensure_in_workspace(fp)
    if is_immutable(fp):
        raise PermissionError(f"不可变安全核，禁止修改: {path}")
    if not fp.exists() or not fp.is_file():
        raise FileNotFoundError(f"文件不存在或不是文件: {path}")
    if not old:
        raise ValueError("old 不能为空")
    text = fp.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise ValueError("未找到要替换的原文——注意空格/缩进/换行需逐字匹配（建议先 read_file 拷贝）")
    if count > 1 and not replace_all:
        raise ValueError(f"原文匹配到 {count} 处、不唯一：加上下文使其唯一，或传 replace_all=true 全改")
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    backup = _backup(fp)
    fp.write_text(new_text, encoding="utf-8")
    err = _post_write_check(fp, backup)  # .py 语法/核心文件 import 冒烟，失败已自动回滚
    if err:
        raise RuntimeError(err)
    n = count if replace_all else 1
    r = f"已编辑: {fp}（替换 {n} 处）"
    try:
        from .file import _git_diff
        diff = _git_diff(fp)
        if diff:
            r += "\n" + diff
    except Exception:
        pass
    return r


@tk.tool(label="建目录", group="core", params={"path": "要创建的目录路径"})
def make_dir(path: str) -> str:
    """创建目录（含父目录，已存在不报错）。"""
    d = _resolve(path)
    _ensure_in_workspace(d)
    d.mkdir(parents=True, exist_ok=True)
    return f"已创建目录: {d}"


TOOLS_FILES_MORE = tk.schemas


def execute(name: str, args: dict) -> str | None:
    return tk.execute(name, args)
