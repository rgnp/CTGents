"""L0 结构不变量 — 不执行业务代码，只对源码做 AST / 元数据级断言。

这一层堵的是「单元测试天然看不见的缝」：进化制造的是结构漂移（越界导入、
自画像与实现脱节、契约被破坏），而非单个函数的逻辑错。每条断言都"一条顶
一类"，无论进化写出什么代码都必须成立。

本季实际踩过、由此固化的坑：
- tracker 越界相对导入（from ..tracker 写在 src/ 顶层文件）→ 运行时才崩
  → test_no_beyond_package_relative_imports
- self 自画像虚构 tracker/reflect 子系统（代码从未实现）
  → test_portrait_connections_reference_real_subsystems

派发契约（execute 对外来工具名须返回 None）也属 L0，已在
test_tool_meta.py::TestDispatchContract，此处不重复。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from src.tools.self import SYSTEM_MAP

_SRC = Path(__file__).resolve().parent.parent / "src"
_ROOT = _SRC.parent


def _src_py_files() -> list[Path]:
    return [p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts]


# ── 导入卫生：相对导入不得越过顶层包 ────────────────────────────

def test_no_beyond_package_relative_imports():
    """相对导入点数不得超过该文件在包内的深度（否则越过顶层包 src）。

    src/llm.py 的合法上限是 level 1（from .）；from ..X 会越过 src，import 期
    或运行期抛 "attempted relative import beyond top-level package"。
    src/tools/x.py 上限是 level 2（from ..config = src.config 合法）。
    """
    offenders: list[str] = []
    for f in _src_py_files():
        parts = f.relative_to(_ROOT).parts  # 含顶层包名 "src"
        max_level = len(parts) - 1          # 所在包的组件数（src=1, src.tools=2）
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > max_level:
                dots = "." * node.level
                offenders.append(
                    f"{f.relative_to(_ROOT)}:{node.lineno} "
                    f"level={node.level}>上限{max_level}（from {dots}{node.module or ''}）"
                )
    assert not offenders, (
        "越界相对导入（beyond top-level package）——进化常在顶层文件误用 from ..:\n"
        + "\n".join(offenders)
    )


# ── 自画像保真：connections 只能引用真实存在的子系统 ──────────────

# 非模块的概念性引用（目录等），需显式登记，防止"撒谎"混进白名单
_CONCEPTUAL_REFS = {"knowledge"}


def _ref_resolves(key: str) -> bool:
    """连接键是否对应真实子系统：SYSTEM_MAP 键 / src 模块 / 工具模块 / 概念白名单。"""
    if key in SYSTEM_MAP:
        return True
    if (_SRC / f"{key}.py").exists():          # config、main、tools/__init__、tools/file…
        return True
    if (_SRC / "tools" / f"{key}.py").exists():  # storm、rag、self、git… 裸工具名
        return True
    return key in _CONCEPTUAL_REFS


def test_portrait_connections_reference_real_subsystems():
    """SYSTEM_MAP 每个 connections 键都必须能解析到真实模块/子系统。

    通用化 test_self.py 里针对 tracker/reflect 的硬编码断言：自进化的 agent
    会读自画像来推理自身，一旦自述里出现不存在的子系统（如曾经的 tracker/
    reflect），它就在错误的自我认知上运转。
    """
    bad: list[str] = []
    for sysname, info in SYSTEM_MAP.items():
        for dep in info.get("connections", {}):
            if not _ref_resolves(dep):
                bad.append(f"{sysname}.connections → '{dep}'（无对应模块/子系统）")
    assert not bad, (
        "自画像引用了不存在的子系统（自述与实现脱节）:\n" + "\n".join(bad)
    )


# ── 标记契约：strip 过滤所依赖的 _xxx 标记必须有生产者 ────────────

# log 清扫的惯用形：[m for m in ctx.log if not m.get("_xxx")]
_STRIP_MARKER_RE = re.compile(r'not m\.get\("(_\w+)"\)')


def test_strip_markers_have_producers():
    """strip-then-append 用的每个 `_xxx` 标记，src 里必须存在 `"_xxx":` 生产点。

    踩过的坑：llm.py 按 _task_ctx 剥旧任务消息，但 make_task_context_message
    返回的消息从未带过这个键 → 剥除空转、活跃任务期间上下文每轮堆一份副本
    （全在挂尾区每请求重算，实测单场平均每请求多 miss 上万 token）。
    strip 与 producer 是跨文件契约，单元测试天然看不见 → 固化为结构不变量。
    """
    sources = {f: f.read_text(encoding="utf-8") for f in _src_py_files()}
    all_text = "\n".join(sources.values())
    offenders: list[str] = []
    for f, text in sources.items():
        for marker in set(_STRIP_MARKER_RE.findall(text)):
            if f'"{marker}":' not in all_text:
                offenders.append(
                    f"{f.relative_to(_ROOT)}: strip 标记 {marker} 无任何生产点"
                )
    assert not offenders, (
        "strip 过滤的标记没有生产者（剥除空转 → 消息每轮堆积）:\n"
        + "\n".join(offenders)
    )
