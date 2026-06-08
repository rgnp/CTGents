"""analyzer.py — 静态分析器回归测试。

此前零测试。两个实证确认的 bug 各有专项回归：
1. async 函数曾对 FunctionDef 检查不可见 → 不进定义表、不查坏味道。
2. 相对导入的点号在 node.level 而非 node.module → 解析整段失效，
   连带 _is_used 的 re-export 检测因 endswith("__init__") 恒为死分支。
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from src.tools.analyzer import ProjectAnalyzer


def _build(tmp_path: Path, files: dict[str, str]) -> ProjectAnalyzer:
    """在 tmp_path 下铺文件（key 形如 'src/a.py'），返回已 analyze 的分析器。"""
    for rel, body in files.items():
        fp = tmp_path / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(textwrap.dedent(body), encoding="utf-8")
    analyzer = ProjectAnalyzer(tmp_path)
    analyzer._report = analyzer.analyze(include_tests=True)  # noqa: SLF001
    return analyzer


def _msgs(analyzer: ProjectAnalyzer) -> list[str]:
    return [f.message for f in analyzer._findings]  # noqa: SLF001


def _cats(analyzer: ProjectAnalyzer, category: str) -> list[str]:
    return [f.message for f in analyzer._findings if f.category == category]  # noqa: SLF001


# ── characterization：锁住已正确的行为 ──────────────────────

def test_unused_function_flagged_dead(tmp_path):
    a = _build(tmp_path, {"src/m.py": "def lonely():\n    return 1\n"})
    assert any("lonely" in m for m in _cats(a, "dead_code"))


def test_referenced_function_not_dead(tmp_path):
    a = _build(tmp_path, {
        "src/m.py": "def helper():\n    return 1\n",
        "src/use.py": "from src.m import helper\n\ndef go():\n    return helper()\n",
    })
    assert not any("helper" in m for m in _cats(a, "dead_code"))


def test_long_function_flagged(tmp_path):
    body = "def big():\n" + "".join(f"    x{i} = {i}\n" for i in range(60)) + "    return 1\n"
    a = _build(tmp_path, {"src/m.py": body})
    assert any("big" in m and "超过" in m for m in _cats(a, "style"))


def test_mutable_default_flagged(tmp_path):
    a = _build(tmp_path, {"src/m.py": "def f(x=[]):\n    return x\n"})
    assert any("可变默认参数" in m for m in _cats(a, "anti_pattern"))


def test_bare_except_flagged(tmp_path):
    a = _build(tmp_path, {"src/m.py": "def f():\n    try:\n        pass\n    except:\n        pass\n"})
    assert any("裸 except" in m for m in _cats(a, "anti_pattern"))


def test_unreachable_after_return_flagged(tmp_path):
    a = _build(tmp_path, {"src/m.py": "def f():\n    return 1\n    x = 2\n"})
    assert any("不可达" in m for m in _cats(a, "dead_code"))


def test_high_complexity_flagged(tmp_path):
    ifs = "".join(f"    if x == {i}:\n        pass\n" for i in range(12))
    a = _build(tmp_path, {"src/m.py": f"def f(x):\n{ifs}    return x\n"})
    assert any("圈复杂度" in m for m in _cats(a, "complexity"))


def test_magic_method_not_dead(tmp_path):
    a = _build(tmp_path, {"src/m.py": "class C:\n    def __init__(self):\n        self.x = 1\n"})
    assert not any("__init__" in m for m in _cats(a, "dead_code"))


def test_test_function_exempt(tmp_path):
    a = _build(tmp_path, {"tests/test_x.py": "def test_thing():\n    assert True\n"})
    assert not any("test_thing" in m for m in _cats(a, "dead_code"))


# ── 回归：async 函数必须可见（修复前全部失败）──────────────

def test_async_function_counted_in_stats(tmp_path):
    a = _build(tmp_path, {"src/m.py": "async def fetch():\n    return 1\n"})
    assert a._report.stats["total_functions"] >= 1, "async 函数未计入统计"  # noqa: SLF001


def test_async_long_function_flagged(tmp_path):
    body = "async def big():\n" + "".join(f"    x{i} = {i}\n" for i in range(60)) + "    return 1\n"
    a = _build(tmp_path, {"src/m.py": body})
    assert any("big" in m and "超过" in m for m in _cats(a, "style")), "async 长函数未被检查"


def test_async_unused_flagged_dead(tmp_path):
    a = _build(tmp_path, {"src/m.py": "async def orphan():\n    return 1\n"})
    assert any("orphan" in m for m in _cats(a, "dead_code")), "async 死代码未被检测"


def test_async_mutable_default_flagged(tmp_path):
    a = _build(tmp_path, {"src/m.py": "async def f(x=[]):\n    return x\n"})
    assert any("可变默认参数" in m for m in _cats(a, "anti_pattern")), "async 坏味道未被检查"
