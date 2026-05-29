"""Plugin 系统：agent 可自学新能力并以 Python 代码装给自己。"""

import importlib.util
import logging
import sys
import textwrap
from pathlib import Path

from ..config import PLUGINS_DIR

logger = logging.getLogger(__name__)

_plugins: dict[str, object] = {}  # name -> module


# ═══════════════════════════════════════════════════════════════
# 接口规范
# ═══════════════════════════════════════════════════════════════

PLUGIN_SPEC = textwrap.dedent("""\
# Plugin 接口规范 v1.0

## 必需接口

### 1. TOOLS — 工具定义列表
```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "工具描述",
            "parameters": {"type": "object", "properties": {...}, "required": [...]}
        }
    },
]
```

### 2. execute(name, args) — 工具调度
```python
def execute(name: str, args: dict) -> str | None:
    if name == "my_tool":
        return f"结果: {args}"
    return None
```

### 3. DESCRIPTION — 插件能力概述
```python
DESCRIPTION = "提供文本统计能力：单词数、字符数、句子数、段落数。"
```
此字段是 LLM 判断是否调用此插件的入口。系统会在发现阶段展示所有插件的 DESCRIPTION，
LLM 匹配到相关描述后再查看具体 TOOLS。务必写清楚「能做什么、什么时候用」。

## 可选接口

### 4. COMMANDS — 指令扩展
```python
# 插件可以注册新的 / 指令。安装后系统自动加载。
# handler 签名: (CmdResult, messages: list[dict], args: list[str], session_id: str | None) -> None
COMMANDS = {
    "/mycmd": _handle_my_command,
}

def _handle_my_command(r, msgs, args, sid):
    \"\"\"指令处理函数。修改 CmdResult 的字段来控制行为。\"\"\"
    r.message = "指令已执行"
```
系统在插件安装和启动时自动调用 register_plugin_commands() 加载所有 COMMANDS。

### 5. on_load() — 加载回调
""")


def get_plugin_spec() -> str:
    return PLUGIN_SPEC


def execute(name: str, args: dict) -> str | None:
    if name == "install_plugin":
        result = install_plugin(args["name"], args["code"])
        _reload_all()
        return result
    if name == "list_plugins":
        return list_plugins()
    if name == "plugin_spec":
        return get_plugin_spec()
    return None


def _reload_all() -> None:
    """重扫描所有插件，刷新工具列表和指令。"""
    global _plugin_tools
    _plugin_tools = discover_plugins()
    _install_commands()


def _install_commands() -> None:
    """将插件的 COMMANDS 注册到指令系统。"""
    try:
        from ..commands import register_plugin_commands
        register_plugin_commands()
    except Exception:
        pass


_plugin_tools: list[dict] = []


def get_plugin_tools() -> list[dict]:
    return _plugin_tools


def reload_plugins() -> None:
    _reload_all()


def discover_plugins() -> list[dict]:
    """扫描 plugins/ 目录，加载所有 .py 插件，返回聚合的 TOOLS 列表。"""
    dirpath = Path(PLUGINS_DIR)
    if not dirpath.exists():
        dirpath.mkdir(parents=True)

    all_tools: list[dict] = []
    seen: set[str] = set()
    for f in sorted(dirpath.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        try:
            mod = _load_module(f.stem, f)
            if hasattr(mod, "TOOLS"):
                for t in mod.TOOLS:
                    name = t.get("function", {}).get("name", "")
                    if name in seen:
                        logger.warning("插件 %s 的工具 %s 与已有工具重名，已跳过", f.stem, name)
                        continue
                    seen.add(name)
                    all_tools.append(t)
        except Exception:
            logger.warning("加载插件失败: %s", f.stem, exc_info=True)

    return all_tools


def _load_module(name: str, filepath: Path) -> object:
    mod_name = f"plugin_{name}"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _plugins[name] = mod
    return mod


def install_plugin(name: str, code: str) -> str:
    """写入插件文件并热加载，返回状态消息。"""
    dirpath = Path(PLUGINS_DIR)
    dirpath.mkdir(parents=True, exist_ok=True)

    filepath = dirpath / f"{name}.py"
    filepath.write_text(code, encoding="utf-8")

    try:
        _load_module(name, filepath)
        _register_plugin_commands(name)
        return f"插件已安装并激活: {name}"
    except Exception as e:
        return f"插件安装失败（代码有误）: {e}"


def _register_plugin_commands(name: str) -> None:
    """如果插件定义了 COMMANDS，注册到全局指令系统。"""
    mod = _plugins.get(name)
    if mod is None or not hasattr(mod, "COMMANDS"):
        return
    # 延迟导入避免循环依赖
    from ..commands import COMMANDS
    for cmd_name, handler in mod.COMMANDS.items():
        COMMANDS[cmd_name] = handler


def list_plugins() -> str:
    """列出已安装的插件及其工具。"""
    dirpath = Path(PLUGINS_DIR)
    if not dirpath.exists() or not list(dirpath.glob("*.py")):
        return "尚未安装任何插件。\n\n你可以上网学习后自己写 Python 插件，用 install_plugin 安装。\n插件规范：定义 TOOLS 列表 + execute(name, args) 函数。"
    lines = ["已安装插件："]
    for f in sorted(dirpath.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        mod = _plugins.get(f.stem)
        tool_count = len(mod.TOOLS) if mod and hasattr(mod, "TOOLS") else 0
        size = f.stat().st_size
        lines.append(f"  {f.stem}  ({tool_count} 个工具, {size}B)")
    return "\n".join(lines)


def execute_plugin(name: str, args: dict) -> str | None:
    """执行插件工具。遍历已加载插件，找到匹配的 execute。"""
    for mod in _plugins.values():
        if not hasattr(mod, "execute"):
            continue
        # 检查该插件是否声明了此工具
        declared = hasattr(mod, "TOOLS") and any(
            t.get("function", {}).get("name") == name for t in mod.TOOLS
        )
        if declared:
            try:
                result = mod.execute(name, args)
                if result is not None:
                    return str(result)
                return f"插件工具 {name} 的 execute() 返回了 None（可能未实现该工具的分支）"
            except Exception as e:
                return f"插件 {name} 执行出错: {e}"
    return None
