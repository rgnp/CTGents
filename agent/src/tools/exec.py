"""Shell 执行工具：Python 代码执行 + 通用命令执行 + 安全控制。"""

import os
import platform
import shlex
import subprocess
from pathlib import Path

from ..config import MAX_EXEC_TIMEOUT

# ── 安全配置 ──

# 禁止执行的命令（黑名单）
BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "rmdir /s /q",  # 删库跑路
    "format", "mkfs", "dd if=",              # 格式化磁盘
    "shutdown", "reboot", "poweroff",        # 关机
    "wget ", "curl ",                        # 下载（改成白名单模式，允许某些）
    "sudo ",                                 # 提权
]

# 只允许这些命令（空列表 = 不限制，只用黑名单）
# 设置为允许的通配模式，如 ["npm", "pip", "git", "node", "python"...]
ALLOWED_COMMANDS: list[str] = []  # 空列表表示不限制，只用黑名单

# 最大输出长度（字符）
MAX_OUTPUT_LENGTH = 100_000


# ── 工具定义 ──

TOOLS_EXEC = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "执行 Python 代码并返回输出。"
                "用于数据处理、计算验证、自动化操作等。"
                "代码在子进程中运行，有超时限制。"
                "不要生成需要用户交互的代码（如 input()、GUI 窗口），"
                "可视化请保存为图片文件或输出文本/数值结果。"
            ),
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
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在终端中执行任意 Shell 命令并返回输出。"
                "用于运行构建工具（npm/pip/make）、版本控制（git）、"
                "文件操作、启动服务、查看系统信息等。"
                "命令在项目目录下执行，有超时和输出长度限制。"
                "禁止的命令会被自动拦截。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令（如 'npm test'、'git status'、'pip list'）",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30。长任务（如安装依赖）可设更大值",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录，默认当前项目目录",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


# ── 安全检测 ──


def _is_blocked(command: str) -> tuple[bool, str]:
    """检查命令是否被禁止。返回 (是否禁止, 原因)。"""
    cmd_lower = command.lower().strip()

    # 黑名单检查
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return True, f"命令包含禁止操作: {blocked}"

    # 白名单检查（仅当 ALLOWED_COMMANDS 非空时生效）
    if ALLOWED_COMMANDS:
        first_word = shlex.split(command)[0] if shlex.split(command) else ""
        allowed = False
        for pattern in ALLOWED_COMMANDS:
            if first_word == pattern or first_word.startswith(pattern):
                allowed = True
                break
        if not allowed:
            return True, f"命令不在允许列表中: {first_word}"

    return False, ""


def _truncate_output(output: str, max_len: int = MAX_OUTPUT_LENGTH) -> str:
    """截断过长的输出。"""
    if len(output) <= max_len:
        return output
    return output[:max_len] + f"\n\n...（输出已截断，共 {len(output)} 字符，仅显示前 {max_len} 字符）"


# ── 执行函数 ──


def run_python(code: str) -> str:
    """在子进程中执行 Python 代码，捕获 stdout/stderr。"""
    env = {**os.environ, "MPLBACKEND": "Agg"}

    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
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

    # ── 确定 Shell ──
    shell = "cmd" if platform.system() == "Windows" else "bash"

    # ── 执行 ──
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
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
