"""Plugin 系统：agent 可自学新能力并以 Python 代码装给自己。"""

import importlib.util
import logging
import sys
from pathlib import Path

from ..config import PLUGINS_DIR

logger = logging.getLogger(__name__)

_plugins: dict[str, object] = {}  # name -> module


def discover_plugins() -> list[dict]:
    """扫描 plugins/ 目录，加载所有 .py 插件，返回聚合的 TOOLS 列表。"""
    dirpath = Path(PLUGINS_DIR)
    if not dirpath.exists():
        dirpath.mkdir(parents=True)

    all_tools: list[dict] = []
    for f in sorted(dirpath.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        try:
            mod = _load_module(f.stem, f)
            if hasattr(mod, "TOOLS"):
                all_tools.extend(mod.TOOLS)
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
        return f"插件已安装并激活: {name}"
    except Exception as e:
        return f"插件安装失败（代码有误）: {e}"


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
        if hasattr(mod, "execute"):
            try:
                result = mod.execute(name, args)
                if result is not None:
                    return str(result)
            except Exception as e:
                return f"插件执行出错: {e}"
    return None
