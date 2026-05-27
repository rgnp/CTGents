import subprocess

TOOLS_CODE = [
    {
        "type": "function",
        "function": {
            "name": "grep_code",
            "description": (
                "在项目中搜索代码。支持正则表达式，返回匹配的文件和行。"
                "用于查找函数定义、变量引用、配置项等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式，支持正则表达式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索目录，不传则搜索当前目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


def grep_code(pattern: str, path: str | None = None) -> str:
    """用 ripgrep/rg 或 findstr 搜索代码。"""
    search_dir = path or "."

    # Linux/macOS: 优先用 ripgrep (最快)，其次 grep
    # Windows: 用 findstr（内置）
    commands = [
        ["rg", "--no-heading", "-n", pattern, search_dir],
        ["grep", "-rn", "--include=*.py", pattern, search_dir],
        ["findstr", "/s", "/n", "/i", pattern, f"{search_dir}\\*.py"],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout.strip()
            if output:
                lines = output.split("\n")
                if len(lines) > 50:
                    output = "\n".join(lines[:50]) + f"\n\n...（共 {len(lines)} 条，已截断至前 50 条）"
                return output
            return f"未找到匹配「{pattern}」的结果。"
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return f"搜索超时: {pattern}"

    return "系统中未找到 grep/rg/findstr，无法搜索代码。"
