import os
import shlex
import subprocess
from pathlib import Path

from ..config import MAX_EXEC_TIMEOUT
from ..params import RUNTIME

# ── 安全配置 ──

# 危险命令关键词 — 匹配整个命令字符串（含子串）。只放明确危险的全命令模式。
_DANGEROUS_PATTERNS = [
    # 删库跑路（必须先解析才能拦 rm /rmdir，此处只拦明确的全路径破坏模式）
    "rm -rf /", "rm -rf /*", "rmdir /s /q",
    "format", "mkfs", "dd if=",              # 格式化磁盘
    "shutdown", "reboot", "poweroff",        # 关机
    "wget ", "curl ",                        # 下载
    "sudo ",                                 # 提权
]

# 文件操作的命令名 — 只检查第一个词（而非子串），不误拦 `git rm` 之类。
_BLOCKED_FILE_OPS = frozenset({
    "rm", "del", "rd", "rmdir",          # 删除 — 走 delete_file 门禁
    "move", "ren", "rename", "copy", "xcopy",  # 移动/复制 — 走 write_file 门禁
})
# 最大输出长度（字符）
MAX_OUTPUT_LENGTH = 100_000
SHELL_META_CHARS = frozenset("&|;<>\n\r")


# ── 工具定义 ──

TOOLS_EXEC = [
    {
        "_meta": {"label": "执行代码", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "执行 Python 代码并返回输出。子进程运行，有超时，禁止交互/窗口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "_meta": {"label": "执行命令", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 Shell 命令。构建/测试/git/文件操作等，有超时和输出限制。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell 命令，如 'pytest -q'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录，默认当前项目",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


# ── 安全检测 ──


def _is_blocked(command: str) -> tuple[bool, str]:
    """检查命令是否被禁止。返回 (是否禁止, 原因)。

    文件操作命令（rm/del/copy等）只检查命令本身（第一个词），不误拦 git rm；
    危险模式（rm -rf / 等）匹配整条命令字符串。
    """
    cmd_lower = command.lower().strip()

    # 危险模式：匹配整条命令
    for pat in _DANGEROUS_PATTERNS:
        if pat in cmd_lower:
            return True, f"命令包含禁止操作: {pat}"

    # 文件操作命令：只检查第一个词（不是子串，不误拦 git rm / copy 等）
    try:
        parts = shlex.split(cmd_lower)
    except ValueError:
        parts = cmd_lower.split()
    if parts and parts[0] in _BLOCKED_FILE_OPS:
        return True, (
            f"禁止直接执行 {parts[0]} 命令"
            f" - 文件增删改必须走工具 API（write_file/delete_file/edit_file_lines）。"
            f" git {parts[0]} 等子命令不受此限。"
        )

    return False, ""


def _check_git_hook_bypass(parts: list[str]) -> str:
    """拦截绕过 git 钩子（质量门）的命令。返回拒绝理由，合法返回空串。

    门超时 ≠ 门失败：质量门要跑全量测试（~40s+），timeout 不够请加大或
    不传（commit 有自动地板）。人工放行（--no-verify）只能由用户在终端执行。
    模式拦不全没关系——门通行证审计（gate_audit）按提交树哈希兜底。
    """
    if not parts or Path(parts[0]).stem.lower() != "git":
        return ""
    # -c core.hooksPath=... 等配置覆盖 = 换掉钩子本体
    joined = " ".join(parts).lower()
    if "core.hookspath" in joined:
        return "git 命令覆盖 core.hooksPath = 替换质量门钩子"
    if "commit" in parts and ("--no-verify" in parts or "-n" in parts):
        return "git commit --no-verify/-n 绕过质量门"
    if "push" in parts and "--no-verify" in parts:
        return "git push --no-verify 绕过钩子"
    return ""


def _truncate_output(output: str, max_len: int = MAX_OUTPUT_LENGTH) -> str:
    """截断过长的输出。"""
    if len(output) <= max_len:
        return output
    return output[:max_len] + f"\n\n...（输出已截断，共 {len(output)} 字符，仅显示前 {max_len} 字符）"


def _split_command(command: str) -> list[str] | str:
    """解析单个命令，拒绝需要 shell 解释的语法。"""
    if any(ch in command for ch in SHELL_META_CHARS):
        return "命令包含 shell 元字符（& | ; < > 或换行），请改用单个可执行文件加参数。"
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"命令解析失败: {exc}"
    if not parts:
        return "命令为空"
    return parts


# ── 执行函数 ──


def run_python(code: str) -> str:
    """在子进程中执行 Python 代码，捕获 stdout/stderr。"""
    env = {**os.environ, "MPLBACKEND": "Agg"}

    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=MAX_EXEC_TIMEOUT,
            cwd=Path.cwd(),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"代码执行超时（{MAX_EXEC_TIMEOUT} 秒）"
    except FileNotFoundError:
        return "找不到 Python 解释器"
    except OSError as e:
        return f"执行失败: {e}"

    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")

    output = "\n".join(parts) if parts else "(无输出)"
    return _truncate_output(output)


def run_command(command: str, timeout: int = 30, workdir: str | None = None) -> str:
    """在终端中执行任意 Shell 命令。

    Args:
        command: 要执行的命令
        timeout: 超时秒数，默认 30
        workdir: 工作目录，默认当前项目目录

    Returns:
        命令输出（stdout + stderr）

    """
    # ── 安全检查 ──
    is_blocked, reason = _is_blocked(command)
    if is_blocked:
        return f"⛔ 命令被拦截: {reason}\n命令: {command}"

    # ── 确定工作目录 ──
    cwd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
    if not cwd.exists():
        return f"工作目录不存在: {cwd}"
    if not cwd.is_dir():
        return f"路径不是目录: {cwd}"

    cmd_parts = _split_command(command)
    if isinstance(cmd_parts, str):
        return cmd_parts

    bypass_reason = _check_git_hook_bypass(cmd_parts)
    if bypass_reason:
        return (
            f"⛔ 命令被拦截: {bypass_reason}。\n"
            f"门超时不等于门失败——质量门要跑全量测试（~40s+）。正道：\n"
            f"1. 用 git_commit 工具提交（推荐），或 run_command 不传 timeout"
            f"（commit 自动抬到 {RUNTIME.git_commit_timeout_floor}s 地板）；\n"
            f"2. 门真失败时，修复失败的测试/lint，再提交；\n"
            f"3. 人工放行是用户的决定——向用户报告障碍，不要替用户决定绕过。"
        )

    # git commit 超时地板：钩子门 ~40s+，超时给小了正道必死 → 推向绕门
    if cmd_parts and Path(cmd_parts[0]).stem.lower() == "git" and "commit" in cmd_parts:
        timeout = max(timeout, RUNTIME.git_commit_timeout_floor)

    # ── 执行 ──
    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return f"命令执行超时（{timeout} 秒）: {command}"
    except OSError as e:
        return f"执行失败: {e}"

    # ── 格式化输出 ──
    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")

    output = "\n".join(parts) if parts else "(无输出)"

    # 如果命令失败，在输出前加入退出码提示
    if result.returncode != 0:
        output = f"退出码: {result.returncode}\n\n" + output

    return _truncate_output(output)


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "run_python":
        return run_python(args["code"])
    if name == "run_command":
        return run_command(
            args["command"],
            args.get("timeout", 30),
            args.get("workdir"),
        )
    return None
