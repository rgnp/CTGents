import subprocess
from pathlib import Path

from ..config import MAX_EXEC_TIMEOUT

TOOLS_EXEC = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "执行 Python 代码并返回输出。"
                "用于数据处理、计算验证、自动化操作等。"
                "代码在子进程中运行，有超时限制，无法访问网络。"
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
]


def run_python(code: str) -> str:
    """在子进程中执行 Python 代码，捕获 stdout/stderr。"""
    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=MAX_EXEC_TIMEOUT,
            cwd=Path.cwd(),
        )
    except subprocess.TimeoutExpired:
        return f"代码执行超时（{MAX_EXEC_TIMEOUT} 秒限制）"
    except FileNotFoundError:
        return "找不到 Python 解释器，请确认已安装 Python"

    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")

    return "\n".join(parts) if parts else "(无输出)"
