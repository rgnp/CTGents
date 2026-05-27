from pathlib import Path

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
            "name": "write_file",
            "description": "创建或覆写本地文件。用于保存报告、代码、笔记等。写入前会告知用户即将写入的路径。",
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
            "description": "删除指定文件。用于清理临时文件、测试脚本等。删除前会告知用户。不可恢复，谨慎使用。",
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
]


def read_file(path: str) -> str:
    """读取本地文件内容，返回原始结果（截断由调用方处理）。"""
    filepath = Path(path).expanduser().resolve()
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    try:
        return filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"无法以 UTF-8 编码读取: {path}（可能是二进制文件）"


def write_file(path: str, content: str) -> str:
    """写入文件。"""
    filepath = Path(path).expanduser().resolve()
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        return f"已写入: {filepath}（{len(content)} 字符）"
    except OSError as e:
        return f"写入失败: {e}"


def list_files(path: str | None) -> str:
    """列出目录内容。"""
    dirpath = Path(path).expanduser().resolve() if path else Path.cwd()
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

    return "\n".join(lines)


def delete_file(path: str) -> str:
    """删除文件。"""
    filepath = Path(path).expanduser().resolve()
    if not filepath.exists():
        return f"文件不存在: {path}"
    if not filepath.is_file():
        return f"路径不是文件: {path}"
    try:
        filepath.unlink()
        return f"已删除: {filepath}"
    except OSError as e:
        return f"删除失败: {e}"
