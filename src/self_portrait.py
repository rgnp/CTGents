"""自画像 — 运行此脚本即可获得项目的完整认知。

用法:
    py src/self_portrait.py          # 完整报告
    py src/self_portrait.py --short  # 仅摘要
    py src/self_portrait.py --json   # JSON 输出（供程序消费）
"""

import ast
import json as _json
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

# 确保项目在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SRC_DIR = PROJECT_ROOT / "src"

_STDLIB_TOP = frozenset({
    "os", "sys", "re", "json", "ast", "time", "subprocess",
    "pathlib", "logging", "typing", "collections", "functools",
    "dataclasses", "abc", "uuid", "math", "traceback", "threading",
    "configparser", "importlib", "shutil", "contextlib", "hashlib",
    "argparse", "datetime", "enum", "types", "textwrap", "inspect",
    "copy", "glob", "io", "itertools", "operator", "pickle", "pprint",
    "random", "socket", "struct", "tempfile", "unittest", "warnings",
    "weakref", "zipfile", "csv", "html", "http", "xml",
    "concurrent", "asyncio", "email", "json", "base64", "hashlib",
    "platform", "signal", "stat", "string", "textwrap",
})


# ═══════════════════════════════════════════════════════════════
# 1. 模块扫描
# ═══════════════════════════════════════════════════════════════

def _first_line(docstring: str) -> str:
    if not docstring:
        return "（无描述）"
    return docstring.strip().split("\n")[0].rstrip("。.")


def _parse_module_summary(filepath: Path) -> dict:
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return {"file": filepath.name, "summary": "（无法解析）", "functions": [], "classes": []}

    doc = ast.get_docstring(tree) or ""
    functions = []
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)

    return {
        "file": filepath.name,
        "summary": _first_line(doc),
        "functions": sorted(set(functions)),
        "classes": sorted(set(classes)),
        "size_lines": len(filepath.read_text(encoding="utf-8").split("\n")),
    }


def scan_modules() -> list[dict]:
    modules = []
    for py_file in sorted(SRC_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        info = _parse_module_summary(py_file)
        info["category"] = "core"
        modules.append(info)

    tools_dir = SRC_DIR / "tools"
    if tools_dir.exists():
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            info = _parse_module_summary(py_file)
            info["category"] = "tool"
            modules.append(info)

    return modules


# ═══════════════════════════════════════════════════════════════
# 2. 工具清单（通过 importlib 加载，避免脆弱的 regex）
# ═══════════════════════════════════════════════════════════════

def scan_tools() -> list[dict]:
    tool_list = []

    # 从 tools/__init__.py 解析模块清单
    tools_init = SRC_DIR / "tools" / "__init__.py"
    if not tools_init.exists():
        return []

    content = tools_init.read_text(encoding="utf-8")
    match = re.search(r"_BUILTIN_MODULES.*?=\s*\[(.*?)\]", content, re.DOTALL)
    if not match:
        return []

    specs = re.findall(r'\("\.(\w+)",\s*"(\w+)",\s*"(\w+)"\)', match.group(1))

    for mod_name, tools_var, exec_var in specs:
        try:
            mod = importlib.import_module(f"src.tools.{mod_name}")
            tools_defs = getattr(mod, tools_var, [])
            for td in tools_defs:
                func = td.get("function", {})
                name = func.get("name", "?")
                desc = _first_line(func.get("description", ""))
                tool_list.append({"name": name, "module": mod_name, "desc": desc})
        except Exception:
            tool_list.append({"name": f"({mod_name} 加载失败)", "module": mod_name, "desc": ""})

    return tool_list


# ═══════════════════════════════════════════════════════════════
# 3. 测试 & 覆盖率
# ═══════════════════════════════════════════════════════════════

def scan_tests() -> dict:
    tests_dir = PROJECT_ROOT / "tests"
    if not tests_dir.exists():
        return {"total": 0, "files": []}

    test_files = sorted(tests_dir.glob("test_*.py"))
    result = {"files": [f.name for f in test_files], "total": 0}

    try:
        proc = subprocess.run(
            ["py", "-m", "pytest", "--collect-only", "-q", "--tb=no"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            timeout=30,
            encoding="utf-8", errors="replace",
        )
        combined = proc.stdout + proc.stderr
        for line in combined.split("\n"):
            m = re.search(r"(\d+) tests?\s+collected|collected (\d+) items?", line)
            if m:
                result["total"] = int(m.group(1) or m.group(2))
                break
    except Exception:
        pass

    return result


def scan_coverage() -> dict:
    cov_json = PROJECT_ROOT / "coverage.json"
    if not cov_json.exists():
        return {"available": False, "reason": "coverage.json 不存在，请先运行 pytest --cov=src --cov-report=json"}

    try:
        data = _json.loads(cov_json.read_text(encoding="utf-8"))
        totals = data.get("totals", {})
        return {
            "available": True,
            "total_pct": round(totals.get("percent_covered", 0.0), 1),
            "statements": totals.get("num_statements", 0),
            "covered": totals.get("covered_lines", 0),
            "missing": totals.get("missing_lines", 0),
        }
    except Exception as e:
        return {"available": False, "reason": f"解析 coverage.json 失败: {e}"}


# ═══════════════════════════════════════════════════════════════
# 4. Git
# ═══════════════════════════════════════════════════════════════

def scan_git() -> dict:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(PROJECT_ROOT), capture_output=True, encoding="utf-8", timeout=5,
        ).stdout.strip() or "unknown"

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT), capture_output=True, encoding="utf-8", timeout=5,
        ).stdout.strip()

        changed = len([l for l in status.split("\n") if l.strip()]) if status else 0

        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(PROJECT_ROOT), capture_output=True, encoding="utf-8", timeout=5,
        ).stdout.strip()

        return {"branch": branch, "changed_files": changed, "recent_commits": log}
    except Exception:
        return {"branch": "unknown", "changed_files": 0, "recent_commits": ""}


# ═══════════════════════════════════════════════════════════════
# 5. 健康检查 — 坏代码检测
# ═══════════════════════════════════════════════════════════════

def scan_failing_tests() -> list[dict]:
    """运行 pytest 收集所有失败测试。"""
    try:
        proc = subprocess.run(
            ["py", "-m", "pytest", "tests/", "-q", "--tb=line"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        failures = []
        for line in proc.stdout.split("\n"):
            if "FAILED" in line:
                parts = line.strip().split("::")
                test_name = parts[-1].split(" ")[0] if parts else line.strip()
                # 提取原因
                reason = ""
                if "Error" in line or "error" in line:
                    reason = line.split("-")[-1].strip() if "-" in line else ""
                failures.append({"test": test_name, "reason": reason})
        return failures
    except Exception as e:
        return [{"test": "pytest 自身失败", "reason": str(e)}]


def scan_untested_sources() -> list[str]:
    """检测 src/ 下有公开函数但没有对应测试文件的模块。"""
    untested = []
    for src_file in sorted(SRC_DIR.glob("*.py")):
        if src_file.name in ("__init__.py",):
            continue
        # 检查是否有对应测试
        stem = src_file.stem
        test_file = PROJECT_ROOT / "tests" / f"test_{stem}.py"
        if not test_file.exists():
            # 检查内容是否值得测试（有公开函数或类）
            try:
                tree = ast.parse(src_file.read_text(encoding="utf-8"))
                has_public = any(
                    isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")
                    for n in ast.walk(tree)
                )
                if has_public:
                    untested.append(f"src/{src_file.name}")
            except Exception:
                pass
    return untested


def scan_dead_imports() -> list[dict]:
    """检测 Python 文件中的死导入（目标模块不存在）。只检查真实的 import 语句。"""
    dead = []
    for py_file in SRC_DIR.glob("**/*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        rel = str(py_file.relative_to(PROJECT_ROOT))

        for node in ast.walk(tree):
            # from X import Y
            if isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                if node.level > 0:
                    continue  # 相对导入 skip
                # 跳过标准库
                top = node.module.split(".")[0]
                if top in _STDLIB_TOP:
                    continue
                try:
                    importlib.import_module(node.module)
                except Exception:
                    dead.append({"file": rel, "import": node.module})
            # import X
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in _STDLIB_TOP:
                        continue
                    try:
                        importlib.import_module(alias.name)
                    except Exception:
                        dead.append({"file": rel, "import": alias.name})
    return dead

def scan_coverage_dead_zones(coverage: dict) -> list[str]:
    """检测覆盖率低于 50% 的模块。"""
    if not coverage.get("available"):
        return []
    red_zones = []
    cov_json = PROJECT_ROOT / "coverage.json"
    try:
        data = _json.loads(cov_json.read_text(encoding="utf-8"))
        for fpath, finfo in data.get("files", {}).items():
            pct = finfo.get("summary", {}).get("percent_covered", 100.0)
            if pct < 50 and fpath.startswith("src/"):
                red_zones.append(f"{fpath} ({pct:.0f}%)")
    except Exception:
        pass
    return red_zones


def scan_function_debt() -> list[dict]:
    """检测测试文件中引用了源代码中不存在的函数（测试写了但功能没做）。"""
    debt = []
    tests_dir = PROJECT_ROOT / "tests"
    for test_file in sorted(tests_dir.glob("test_*.py")):
        try:
            tree = ast.parse(test_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module is None or node.level > 0:
                    continue
                if not node.module.startswith("src."):
                    continue
                try:
                    mod = importlib.import_module(node.module)
                except ImportError:
                    debt.append({
                        "file": test_file.name,
                        "missing_module": node.module,
                        "wanted": ", ".join(a.name for a in node.names),
                    })
                    continue
                # Check each imported name
                for alias in node.names:
                    if not hasattr(mod, alias.name):
                        debt.append({
                            "file": test_file.name,
                            "module": node.module,
                            "missing_name": alias.name,
                        })
    return debt

def render_health(tests_failing, untested_sources, dead_imports, coverage_dead_zones, function_debt) -> str:
    """渲染健康检查报告。"""
    lines = []
    sep = "=" * 60
    lines.append(sep)
    lines.append("  CTGents 健康检查")
    lines.append(sep)
    lines.append("")

    # 汇总
    total_issues = (
        len(tests_failing) + len(untested_sources) + len(dead_imports) +
        len(coverage_dead_zones) + len(function_debt)
    )

    if total_issues == 0:
        lines.append("  ✅ 未发现任何问题。")
        lines.append("")
        lines.append(sep)
        return "\n".join(lines)

    # ── 失败测试 ──
    if tests_failing:
        lines.append(f"🔴 失败测试: {len(tests_failing)} 个")
        lines.append("   这些测试期望的功能尚未实现，是明确的'功能债'。")
        for t in tests_failing[:20]:
            lines.append(f"   · {t['test']}")
            if t.get("reason"):
                lines.append(f"     → {t['reason']}")
        lines.append("")

    # ── 未测试的源文件 ──
    if untested_sources:
        lines.append(f"🟡 无对应测试文件: {len(untested_sources)} 个")
        lines.append("   这些模块有公开接口但缺少测试保护。")
        for f in untested_sources[:15]:
            lines.append(f"   · {f}")
        lines.append("")

    # ── 死导入 ──
    if dead_imports:
        lines.append(f"🔴 死导入: {len(dead_imports)} 处")
        lines.append("   引用了无法导入的模块。")
        for d in dead_imports[:15]:
            lines.append(f"   · {d['file']} → from {d['import']}")
        lines.append("")

    # ── 覆盖率死区 ──
    if coverage_dead_zones:
        lines.append(f"🟡 低覆盖率文件 (<50%): {len(coverage_dead_zones)} 个")
        for z in coverage_dead_zones[:15]:
            lines.append(f"   · {z}")
        lines.append("")

    # ── 功能债 ──
    if function_debt:
        lines.append(f"🔴 功能债（测试引用不存在的函数/模块）: {len(function_debt)} 处")
        for d in function_debt[:20]:
            if "missing_module" in d:
                lines.append(f"   · {d['file']}: import {d['missing_module']} 不存在")
            else:
                lines.append(f"   · {d['file']}: {d['module']}.{d['missing_name']} 不存在")
        lines.append("")

    lines.append(sep)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 5. 渲染
# ═══════════════════════════════════════════════════════════════

def render_full(modules, tools, tests, coverage, git) -> str:
    lines = []
    sep = "=" * 60

    lines.append(sep)
    lines.append("  CTGents 自画像")
    lines.append(sep)
    lines.append(f"  项目:  {PROJECT_ROOT}")
    lines.append(f"  分支:  {git['branch']}  |  未提交: {git['changed_files']} 文件")
    lines.append("")

    # ── 核心模块 ──
    core = [m for m in modules if m["category"] == "core"]
    tools_mods = [m for m in modules if m["category"] == "tool"]

    lines.append("── 核心模块 ──")
    for m in core:
        fns = ", ".join(m["functions"][:8])
        more = f" +{len(m['functions'])-8}" if len(m["functions"]) > 8 else ""
        lines.append(f"  {m['file']:<22s} {m['size_lines']:>4d}行  {m['summary']}")
        if fns:
            lines.append(f"    {'':22s} 接口: {fns}{more}")
    lines.append("")

    # ── 工具模块 ──
    lines.append("── 工具模块 ──")
    for m in tools_mods:
        fns = ", ".join(m["functions"][:6])
        more = f" +{len(m['functions'])-6}" if len(m["functions"]) > 6 else ""
        lines.append(f"  {m['file']:<22s} {m['size_lines']:>4d}行  {m['summary']}")
        if fns:
            lines.append(f"    {'':22s} 接口: {fns}{more}")
    lines.append("")

    # ── 工具清单 ──
    lines.append(f"── 注册工具（共 {len(tools)} 个）──")
    if tools:
        for t in tools:
            lines.append(f"  {t['name']:<30s} [{t['module']}] {t['desc']}")
    else:
        lines.append("  （加载失败）")
    lines.append("")

    # ── 测试 ──
    lines.append("── 测试 ──")
    lines.append(f"  测试文件: {len(tests['files'])} 个  |  用例: {tests['total']} 个")
    lines.append("")

    # ── 覆盖率 ──
    lines.append("── 覆盖率 ──")
    if coverage["available"]:
        pct = coverage["total_pct"]
        if pct >= 75:
            tier = "🔓 tier_3 全解锁"
        elif pct >= 60:
            tier = f"🔓 tier_2（距 tier_3 差 {75 - pct:.1f}%）"
        elif pct >= 45:
            tier = f"🔓 tier_1（距 tier_2 差 {60 - pct:.1f}%）"
        else:
            tier = f"🔓 tier_0（距 tier_1 差 {45 - pct:.1f}%）"

        lines.append(f"  覆盖率: {pct}%  →  {tier}")
        lines.append(f"  语句: {coverage['statements']} | 已覆盖: {coverage['covered']} | 未覆盖: {coverage['missing']}")
    else:
        lines.append(f"  ⚠ {coverage.get('reason', '无法获取覆盖率')}")
    lines.append("")

    # ── Git 最近提交 ──
    if git["recent_commits"]:
        lines.append("── 最近提交 ──")
        for cl in git["recent_commits"].split("\n")[:5]:
            lines.append(f"  {cl}")
        lines.append("")

    # ── 关键文件 ──
    lines.append("── 关键参考文件 ──")
    for path, desc in [
        ("AGENTS.md", "AI 操作手册"),
        ("src/llm.py", "LLM 对话循环"),
        ("src/coverage_gate.py", "函数级关联测试门禁"),
        ("src/guard.py", "崩溃自愈"),
        ("docs/architecture.md", "架构文档"),
        ("docs/features.md", "功能列表"),
        ("requirements.txt", "Python 依赖"),
    ]:
        exists = "✓" if (PROJECT_ROOT / path).exists() else "✗"
        lines.append(f"  {exists} {path:<30s} {desc}")
    lines.append("")

    lines.append(sep)
    return "\n".join(lines)


def render_short(modules, tools, tests, coverage, git) -> str:
    core_count = len([m for m in modules if m["category"] == "core"])
    tool_count = len([m for m in modules if m["category"] == "tool"])
    cov_str = f"{coverage['total_pct']}%" if coverage["available"] else "N/A"

    return (
        f"CTGents | {PROJECT_ROOT}\n"
        f"  模块: {core_count} 核心 + {tool_count} 工具 | "
        f"工具: {len(tools)} 个 | "
        f"测试: {tests['total']} | "
        f"覆盖率: {cov_str} | "
        f"分支: {git['branch']} ({git['changed_files']} 变更)"
    )


def build(scope: str = "full") -> str:
    modules = scan_modules()
    tools = scan_tools()
    tests = scan_tests()
    coverage = scan_coverage()
    git = scan_git()

    if scope == "health":
        tests_failing = scan_failing_tests()
        untested_sources = scan_untested_sources()
        dead_imports = scan_dead_imports()
        coverage_dead_zones = scan_coverage_dead_zones(coverage)
        function_debt = scan_function_debt()
        return render_health(
            tests_failing, untested_sources, dead_imports,
            coverage_dead_zones, function_debt,
        )
    elif scope == "short":
        return render_short(modules, tools, tests, coverage, git)
    elif scope == "json":
        return _json.dumps({
            "project": str(PROJECT_ROOT),
            "modules": modules,
            "tools": tools,
            "tests": tests,
            "coverage": coverage,
            "git": {k: v for k, v in git.items() if k != "recent_commits"},
            "git_recent": git["recent_commits"].split("\n"),
        }, ensure_ascii=False, indent=2)
    else:
        return render_full(modules, tools, tests, coverage, git)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CTGents 自画像")
    parser.add_argument("--short", action="store_true", help="一行摘要")
    parser.add_argument("--json", action="store_true", help="JSON 格式")
    parser.add_argument("--health", action="store_true", help="健康检查：检测失败测试、死导入、未测试文件、功能债等")
    args = parser.parse_args()

    if args.health:
        scope = "health"
    elif args.short:
        scope = "short"
    elif args.json:
        scope = "json"
    else:
        scope = "full"
    print(build(scope))
