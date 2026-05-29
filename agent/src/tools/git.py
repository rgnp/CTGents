"""Git 操作工具：status、diff、add、commit、push、log、branch。"""

import subprocess
from pathlib import Path


GIT_TIMEOUT = 30


TOOLS_GIT = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看 Git 工作区状态。显示当前分支、已修改/已暂存/未跟踪的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "short": {
                        "type": "boolean",
                        "description": "是否使用简短格式（类似 git status --short），默认 false",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看文件变更详情。可查看未暂存（working tree）或已暂存（staged）的差异。",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "是否查看已暂存的变更（--cached），默认 false",
                    },
                    "path": {
                        "type": "string",
                        "description": "只查看特定文件的变更，不传则查看所有",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_add",
            "description": "暂存文件变更（git add）。支持暂存单个文件或所有变更。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要暂存的文件路径或模式。传 '.' 暂存所有变更",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "提交暂存的变更（git commit）。需要提供 commit message。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "commit message。建议格式：<类型>: <简短描述>，如 'feat: 添加 git 操作工具'",
                    },
                    "auto_add": {
                        "type": "boolean",
                        "description": "是否自动暂存所有已修改文件后再提交（git commit -a），默认 false",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "推送本地提交到远程仓库（git push）。可指定远程名和分支名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {
                        "type": "string",
                        "description": "远程仓库名，默认 origin",
                    },
                    "branch": {
                        "type": "string",
                        "description": "分支名，默认当前分支",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "是否强制推送（--force），默认 false。谨慎使用！",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "查看提交历史（git log）。可指定显示条数和格式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "显示最近几条提交，默认 10",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["oneline", "short", "full", "pretty"],
                        "description": "输出格式：oneline（一行）、short（简短）、full（完整）、pretty（默认美观）",
                    },
                    "path": {
                        "type": "string",
                        "description": "只查看特定文件的提交历史",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "查看或创建分支。不传参数时列出所有本地分支；传入新分支名可创建分支。",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_branch": {
                        "type": "string",
                        "description": "要创建的新分支名。不传则只列出分支",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "项目目录，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── 辅助函数 ──


def _git(args: list[str], workdir: str | None = None) -> subprocess.CompletedProcess:
    """执行 git 命令。"""
    cwd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
    return subprocess.run(
        ["git"] + args,
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=cwd,
    )


def _format_result(r: subprocess.CompletedProcess) -> str:
    """格式化 git 命令输出。"""
    parts: list[str] = []
    if r.stdout.strip():
        parts.append(r.stdout.rstrip())
    if r.stderr.strip():
        parts.append(f"[stderr]\n{r.stderr.rstrip()}")
    if r.returncode != 0:
        # 非错误性的 stderr（如 git 的提示信息）不额外标注
        if not parts:
            return f"退出码: {r.returncode}"
    output = "\n".join(parts) if parts else "(无输出)"
    if len(output) > 100_000:
        output = output[:100_000] + f"\n\n...（输出已截断，共 {len(output)} 字符）"
    return output


# ── 工具函数 ──


def git_status(short: bool = False, workdir: str | None = None) -> str:
    """查看工作区状态。"""
    args = ["status"]
    if short:
        args.append("--short")
    r = _git(args, workdir)
    if r.returncode != 0:
        return _format_result(r)
    return _format_result(r)


def git_diff(staged: bool = False, path: str | None = None, workdir: str | None = None) -> str:
    """查看变更详情。"""
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args.append("--")
        args.append(path)
    r = _git(args, workdir)
    return _format_result(r)


def git_add(path: str, workdir: str | None = None) -> str:
    """暂存文件。"""
    r = _git(["add", path], workdir)
    return _format_result(r)


def git_commit(message: str, auto_add: bool = False, workdir: str | None = None) -> str:
    """提交暂存的变更。"""
    args = ["commit"]
    if auto_add:
        args.append("-a")
    args.extend(["-m", message])
    r = _git(args, workdir)
    return _format_result(r)


def git_push(remote: str = "origin", branch: str | None = None,
             force: bool = False, workdir: str | None = None) -> str:
    """推送到远程。"""
    args = ["push"]
    if force:
        args.append("--force")
    args.append(remote)
    if branch:
        args.append(branch)
    r = _git(args, workdir)
    return _format_result(r)


def git_log(count: int = 10, format: str = "pretty",
            path: str | None = None, workdir: str | None = None) -> str:
    """查看提交历史。"""
    fmt_map = {
        "oneline": "%h %s",
        "short": "%h %an %ar%n%s",
        "full": "%H%n%an <%ae>%n%ai%n%s%n%b",
        "pretty": "%C(yellow)%h%Creset %s %C(green)(%ar)%Creset %C(blue)%an%Creset",
    }
    format_str = fmt_map.get(format, fmt_map["pretty"])

    args = ["log", f"-{count}", f"--format={format_str}"]
    if path:
        args.extend(["--", path])
    r = _git(args, workdir)
    if r.returncode != 0:
        return _format_result(r)
    return _format_result(r)


def git_branch(new_branch: str | None = None, workdir: str | None = None) -> str:
    """查看或创建分支。"""
    if new_branch:
        r = _git(["branch", new_branch], workdir)
        if r.returncode == 0:
            return f"已创建分支: {new_branch}\n{_format_result(r)}"
        return _format_result(r)
    else:
        r = _git(["branch"], workdir)
        return _format_result(r)


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "git_status":
        return git_status(args.get("short", False), args.get("workdir"))
    if name == "git_diff":
        return git_diff(args.get("staged", False), args.get("path"), args.get("workdir"))
    if name == "git_add":
        return git_add(args["path"], args.get("workdir"))
    if name == "git_commit":
        return git_commit(args["message"], args.get("auto_add", False), args.get("workdir"))
    if name == "git_push":
        return git_push(
            args.get("remote", "origin"),
            args.get("branch"),
            args.get("force", False),
            args.get("workdir"),
        )
    if name == "git_log":
        return git_log(args.get("count", 10), args.get("format", "pretty"), args.get("path"), args.get("workdir"))
    if name == "git_branch":
        return git_branch(args.get("new_branch"), args.get("workdir"))
    return None
