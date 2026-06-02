"""MCP 协议支持：连接 MCP 服务器，动态注册工具。

依赖（可选）：pip install mcp

支持两种传输模式：
  - stdio：本地子进程通信（如 npx 启动的 MCP 服务器）
  - HTTP/SSE：远程 MCP 服务器
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

# MCP SDK 是可选依赖
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# ── 配置路径 ──
MCP_CONFIG_DIR = Path(os.path.expanduser("~")) / ".ctgents"
MCP_CONFIG_FILE = MCP_CONFIG_DIR / "mcp.json"

# ── 运行时状态 ──
# { name: { session, read, write, tools, transport, server_info } }
_connections: dict[str, dict[str, Any]] = {}


# ── 工具定义 ──

TOOLS_MCP = [
    {
        "type": "function",
        "function": {
            "name": "mcp_connect",
            "description": (
                "连接到 MCP 服务器。stdio 模式通过 command+args 启动本地进程，"
                "http 模式通过 url 连接远程服务。连接后工具自动注册。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "连接名称（唯一标识，用于后续引用和断开）",
                    },
                    "command": {
                        "type": "string",
                        "description": "（stdio 模式）启动命令，如 npx / python / node",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "（stdio 模式）命令参数数组",
                    },
                    "url": {
                        "type": "string",
                        "description": "（http 模式）MCP 服务器 URL",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "（可选）传递给服务器的额外环境变量",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_disconnect",
            "description": "断开与 MCP 服务器的连接，释放资源并移除其工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要断开的连接名称",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_list",
            "description": "列出所有已连接的 MCP 服务器及其提供的工具。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_save_config",
            "description": (
                "将当前的 MCP 服务器配置保存到 ~/.ctgents/mcp.json，"
                "下次启动 Agent 时自动连接。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ── 管理命令执行 ──


def execute(name: str, args: dict) -> str | None:
    """调度 MCP 管理命令。"""
    if not MCP_AVAILABLE:
        if name.startswith("mcp_"):
            return json.dumps(
                {"error": "MCP SDK 未安装，请执行: pip install mcp"}, ensure_ascii=False
            )
        return None

    try:
        if name == "mcp_connect":
            return _connect_sync(**args)
        elif name == "mcp_disconnect":
            return _disconnect_sync(**args)
        elif name == "mcp_list":
            return _list_servers()
        elif name == "mcp_save_config":
            return _save_config()
    except Exception as e:
        return json.dumps({"error": f"MCP 操作失败: {e}"}, ensure_ascii=False)

    return None


# ── 同步包装（MCP SDK 是 async，用 asyncio.run 包装）──


def _connect_sync(
    name: str,
    command: str = None,
    args: list = None,
    url: str = None,
    env: dict = None,
) -> str:
    """同步连接 MCP 服务器。"""
    if name in _connections:
        return json.dumps(
            {"error": f"连接 '{name}' 已存在，请先 mcp_disconnect"}, ensure_ascii=False
        )
    if not command and not url:
        return json.dumps(
            {"error": "需要提供 command（stdio 模式）或 url（http 模式）"},
            ensure_ascii=False,
        )

    try:
        tools_info = asyncio.run(_connect_async(name, command, args or [], url, env or {}))
        return json.dumps(tools_info, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"连接 '{name}' 失败: {e}"}, ensure_ascii=False)


def _disconnect_sync(name: str) -> str:
    """同步断开 MCP 连接。"""
    if name not in _connections:
        return json.dumps({"error": f"连接 '{name}' 不存在"}, ensure_ascii=False)

    try:
        asyncio.run(_disconnect_async(name))
        return json.dumps({"status": "ok", "message": f"已断开 '{name}'"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"断开 '{name}' 失败: {e}"}, ensure_ascii=False)


# ── 异步实现 ──


async def _connect_async(
    name: str,
    command: str | None,
    args: list[str],
    url: str | None,
    env: dict[str, str],
) -> dict:
    """异步连接 MCP 服务器并发现工具。"""
    if url:
        # HTTP/SSE 模式
        read, write = await sse_client(url=url)
    else:
        # stdio 模式
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env={**os.environ, **env} if env else None,
        )
        read, write = await stdio_client(server_params).__aenter__()

    session = await ClientSession(read, write).__aenter__()
    await session.initialize()

    # 发现工具
    tools_result = await session.list_tools()
    tools = []
    for tool in tools_result.tools:
        tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        })

    # 保存连接（不关闭，长期持有）
    _connections[name] = {
        "session": session,
        "read": read,
        "write": write,
        "tools": tools,
        "transport": "http" if url else "stdio",
        "server_info": {
            "command": command,
            "args": args,
            "url": url,
        },
    }

    return {
        "status": "ok",
        "message": f"已连接到 '{name}'（{'HTTP' if url else 'stdio'} 模式）",
        "tools_count": len(tools),
        "tools": [t["name"] for t in tools],
    }


async def _disconnect_async(name: str):
    """异步断开 MCP 连接。"""
    conn = _connections.pop(name)
    session = conn["session"]
    read = conn["read"]
    write = conn["write"]

    await session.__aexit__(None, None, None)
    if hasattr(read, "__aexit__"):
        await read.__aexit__(None, None, None)
    if hasattr(write, "__aexit__"):
        await write.__aexit__(None, None, None)


# ── 查询命令 ──


def _list_servers() -> str:
    """列出所有已连接的 MCP 服务器。"""
    if not _connections:
        return json.dumps(
            {"status": "ok", "message": "没有已连接的 MCP 服务器", "servers": []},
            ensure_ascii=False,
        )

    servers = []
    for name, conn in _connections.items():
        servers.append({
            "name": name,
            "transport": conn["transport"],
            "tools_count": len(conn["tools"]),
            "tools": [t["name"] for t in conn["tools"]],
        })

    return json.dumps({"status": "ok", "servers": servers}, ensure_ascii=False, indent=2)


# ── 配置持久化 ──


def _save_config() -> str:
    """保存 MCP 服务器配置。"""
    if not _connections:
        return json.dumps(
            {"error": "没有已连接的 MCP 服务器可保存"}, ensure_ascii=False
        )

    configs = []
    for name, conn in _connections.items():
        info = conn["server_info"]
        entry = {"name": name}
        if info.get("url"):
            entry["url"] = info["url"]
        else:
            entry["command"] = info["command"]
            entry["args"] = info["args"]
        configs.append(entry)

    MCP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MCP_CONFIG_FILE.write_text(
        json.dumps({"mcp_servers": configs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return json.dumps(
        {
            "status": "ok",
            "message": f"已保存 {len(configs)} 个 MCP 服务器配置到 {MCP_CONFIG_FILE}",
            "configs": configs,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── 供 __init__.py 调用的接口 ──

def get_mcp_tools() -> list[dict]:
    """获取所有已连接 MCP 服务器提供的工具（格式化为 OpenAI function calling 格式）。"""
    if not MCP_AVAILABLE or not _connections:
        return []

    tools = []
    for conn_name, conn in _connections.items():
        for tool in conn.get("tools", []):
            prefixed = {
                "type": "function",
                "function": {
                    "name": f"{conn_name}__{tool['name']}",
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            tools.append(prefixed)
    return tools


def execute_mcp_tool(name: str, args: dict) -> str | None:
    """执行 MCP 工具调用。name 格式: server_name__tool_name"""
    if not MCP_AVAILABLE or not _connections:
        return None

    if "__" not in name:
        return None

    conn_name, tool_name = name.split("__", 1)
    if conn_name not in _connections:
        return None

    conn = _connections[conn_name]
    session: ClientSession = conn["session"]

    try:
        result = asyncio.run(_call_tool_async(session, tool_name, args))
        return result
    except Exception as e:
        return json.dumps({"error": f"MCP 工具 '{tool_name}' 执行失败: {e}"}, ensure_ascii=False)


async def _call_tool_async(session, tool_name: str, args: dict) -> str:
    """异步调用 MCP 工具。"""
    result = await session.call_tool(tool_name, arguments=args)

    # 将 MCP 工具结果转为字符串
    parts = []
    if hasattr(result, "content"):
        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif hasattr(item, "data"):
                parts.append(str(item.data))
            else:
                parts.append(str(item))
    elif hasattr(result, "text"):
        parts.append(result.text)
    else:
        parts.append(str(result))

    return "\n".join(parts) if parts else "（工具执行完成，无输出）"


def load_saved_configs() -> int:
    """启动时自动加载保存的 MCP 服务器配置。
    返回成功连接的服务器数量。"""
    if not MCP_AVAILABLE:
        return 0
    if not MCP_CONFIG_FILE.exists():
        return 0

    try:
        data = json.loads(MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        servers = data.get("mcp_servers", [])
        count = 0
        for svr in servers:
            name = svr["name"]
            if name in _connections:
                continue
            try:
                if "url" in svr:
                    asyncio.run(_connect_async(name, None, [], svr["url"], {}))
                else:
                    asyncio.run(
                        _connect_async(name, svr["command"], svr.get("args", []), None, {})
                    )
                count += 1
            except Exception:
                pass  # 连接失败不阻塞启动
        return count
    except Exception:
        return 0


def get_connection_summary() -> dict[str, str]:
    """返回 MCP 连接摘要：{name: transport_type}，供 /self 使用。"""
    result: dict[str, str] = {}
    for name, info in _connections.items():
        transport = info.get("transport", "?")
        result[name] = transport
    return result
