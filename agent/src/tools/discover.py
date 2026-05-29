"""统一能力发现——一次调用了解所有可用能力。"""

import re
from pathlib import Path


def discover_capabilities() -> str:
    """扫描所有能力目录，返回全景摘要。"""
    lines = []

    # ── 内置工具 ──
    builtin = [
        ("search_web",      "搜索互联网"),
        ("read_page",       "阅读网页全文"),
        ("read_file",       "读取本地文件"),
        ("read_file_lines",  "带行号读取文件"),
        ("write_file",      "写入文件"),
        ("edit_file_lines",  "行级编辑文件"),
        ("undo_edit",       "撤销编辑"),
        ("list_files",      "浏览目录"),
        ("delete_file",     "删除文件"),
        ("count_lines",     "统计文件行数"),
        ("run_python",      "执行 Python 代码"),
        ("run_command",     "执行 Shell 命令"),
        ("grep_code",       "搜索代码"),
        ("think",           "思考与规划"),
        ("remember",        "记住知识"),
        ("recall",          "回忆知识"),
        ("forget",          "忘记知识"),
        ("install_plugin",  "安装新插件"),
        ("list_plugins",    "列出插件"),
        ("plugin_spec",     "插件接口规范"),
        ("git_status",      "Git 工作区状态"),
        ("git_diff",        "Git 文件变更详情"),
        ("git_log",         "Git 提交历史"),
        ("git_commit",      "Git 提交变更"),
        ("git_push",        "Git 推送"),
        ("git_pr",          "Git 创建 Pull Request"),
        ("git_branch",      "Git 分支列表"),
        ("scan_project",    "扫描项目结构和技术栈"),
    ]
    lines.append(f"内置工具 ({len(builtin)} 个)：")
    for name, desc in builtin:
        lines.append(f"  {name} — {desc}")
    lines.append("")

    # ── 插件 ──
    from .plugin_mgr import _plugins
    if _plugins:
        lines.append(f"已安装插件 ({len(_plugins)} 个)：")
        for pname, mod in _plugins.items():
            desc = getattr(mod, "DESCRIPTION", "（无描述）")
            tools = [t["function"]["name"] for t in getattr(mod, "TOOLS", [])]
            lines.append(f"  {pname} — {desc}")
            if tools:
                lines.append(f"    工具: {', '.join(tools)}")
        lines.append("")

    return "\n".join(lines).strip() if lines else "未找到任何能力"


def execute(name: str, args: dict) -> str | None:
    if name == "discover":
        return discover_capabilities()
    return None
