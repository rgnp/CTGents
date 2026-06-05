"""项目静态分析器 — 死代码检测 + 代码坏味道 + 架构理解。

纯 Python AST 实现，零外部依赖。可独立运行或被导入。
用法:
    py src/tools/analyzer.py              # 分析 src/ + tests/
    py src/tools/analyzer.py --json       # JSON 输出
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

MAX_FUNCTION_LINES = 50
MAX_NESTING_DEPTH = 4
MAX_PARAMETERS = 5
HIGH_COMPLEXITY_THRESHOLD = 10

MAGIC_METHODS = frozenset({
    "__init__", "__new__", "__del__", "__repr__", "__str__", "__len__",
    "__getitem__", "__setitem__", "__delitem__", "__iter__", "__next__",
    "__contains__", "__call__", "__enter__", "__exit__", "__eq__", "__ne__",
    "__lt__", "__le__", "__gt__", "__ge__", "__hash__", "__bool__",
    "__add__", "__sub__", "__mul__", "__truediv__", "__getattr__",
    "__setattr__", "__getattribute__", "__post_init__",
})

# 已知注册模式: 函数通过这些模式被间接引用
REGISTRATION_FUNCTIONS = frozenset({
    "_add_cmd", "append", "add", "register", "setdefault",
    "add_handler", "register_tool", "_register_builtin",
})

# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class Finding:
    file: str
    line: int
    category: str
    severity: str
    message: str


@dataclass
class DefInfo:
    name: str
    kind: str
    file: str
    line: int
    end_line: int
    parent: str
    is_public: bool


@dataclass
class AnalysisReport:
    findings: list[Finding] = field(default_factory=list)
    definitions: dict[str, list[DefInfo]] = field(default_factory=lambda: defaultdict(list))
    module_deps: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    stats: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# 主分析器
# ═══════════════════════════════════════════════════════════════


class ProjectAnalyzer:
    """项目级静态分析器。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else Path.cwd()
        self._defs: dict[str, list[DefInfo]] = defaultdict(list)
        self._refs: dict[str, set[str]] = defaultdict(set)
        self._imports: dict[str, dict[str, str]] = defaultdict(dict)
        self._module_deps: dict[str, set[str]] = defaultdict(set)
        self._findings: list[Finding] = []
        self._current_module: str = ""

    # ── 入口 ──────────────────────────────────────────────

    def analyze(self, include_tests: bool = True) -> AnalysisReport:
        self._reset()
        py_files = self._collect_py_files(include_tests)

        # Pass 1: 收集定义和 import
        for fpath in py_files:
            mod = self._file_to_module(fpath)
            self._current_module = mod
            try:
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
                self._collect_definitions(tree, str(fpath), mod)
                self._collect_imports(tree, mod)
            except SyntaxError as e:
                self._findings.append(Finding(
                    file=str(fpath), line=e.lineno or 0,
                    category="syntax", severity="high",
                    message=f"语法错误: {e.msg}",
                ))

        # Pass 2: 收集引用（含注册模式中的间接引用）
        for fpath in py_files:
            mod = self._file_to_module(fpath)
            self._current_module = mod
            try:
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
                self._collect_references(tree, mod)
            except SyntaxError:
                pass

        # 分析
        self._find_dead_code()
        self._find_bad_patterns(py_files)

        return AnalysisReport(
            findings=self._findings,
            definitions=dict(self._defs),
            module_deps=dict(self._module_deps),
            stats=self._compute_stats(),
        )

    def _reset(self) -> None:
        self._defs.clear()
        self._refs.clear()
        self._imports.clear()
        self._module_deps.clear()
        self._findings.clear()
        self._current_module = ""

    # ── 文件收集 ──────────────────────────────────────────

    def _collect_py_files(self, include_tests: bool) -> list[Path]:
        dirs = [self.root / "src"]
        if include_tests:
            tests_dir = self.root / "tests"
            if tests_dir.is_dir():
                dirs.append(tests_dir)
        files: list[Path] = []
        for d in dirs:
            if d.is_dir():
                files.extend(sorted(d.rglob("*.py")))
        return files

    def _file_to_module(self, fpath: Path) -> str:
        try:
            rel = fpath.resolve().relative_to(self.root.resolve())
        except ValueError:
            return fpath.stem
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].replace(".py", "")
        return ".".join(parts)

    # ── Pass 1: 定义收集 ──────────────────────────────────

    def _collect_definitions(self, tree: ast.AST, filepath: str, module: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                parent = self._find_parent_class(node, tree)
                kind = "method" if parent else "function"
                self._defs[module].append(DefInfo(
                    name=node.name, kind=kind, file=filepath,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    parent=parent, is_public=not node.name.startswith("_"),
                ))
            elif isinstance(node, ast.ClassDef):
                self._defs[module].append(DefInfo(
                    name=node.name, kind="class", file=filepath,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    parent="", is_public=not node.name.startswith("_"),
                ))

    def _find_parent_class(self, func_node: ast.FunctionDef, tree: ast.AST) -> str:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in ast.iter_child_nodes(node):
                    if child is func_node:
                        return node.name
        return ""

    def _collect_imports(self, tree: ast.AST, module: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._imports[module][alias.asname or alias.name] = alias.name
                    self._module_deps[module].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                base = self._resolve_relative_import(node.module, module)
                self._module_deps[module].add(base)
                for alias in node.names:
                    imported_name = alias.asname or alias.name
                    self._imports[module][imported_name] = f"{base}.{alias.name}"

    def _resolve_relative_import(self, target: str, current_module: str) -> str:
        if not target.startswith("."):
            return target
        parts = current_module.split(".")
        dots = len(target) - len(target.lstrip("."))
        target_name = target.lstrip(".")
        if dots > len(parts):
            return target_name
        base = ".".join(parts[:-dots]) if dots < len(parts) else ""
        return f"{base}.{target_name}" if base else target_name

    # ── Pass 2: 引用收集 ──────────────────────────────────
    def _collect_references(self, tree: ast.AST, module: str) -> None:
        for node in ast.walk(tree):
            # 普通名称引用
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                self._refs[module].add(node.id)
            # 属性引用
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                full = self._resolve_attr_path(node)
                if full:
                    self._refs[module].add(full)
            # 调用参数中的函数引用
            elif isinstance(node, ast.Call):
                self._collect_call_args_refs(node, module)
            # dict 赋值: _handlers[name] = func
            elif isinstance(node, ast.Assign):
                self._collect_assign_value_refs(node, module)
            # 装饰器: 被装饰函数被装饰器消费，标记为"已引用"
            elif isinstance(node, ast.FunctionDef) and node.decorator_list:
                self._refs[module].add(node.name)

    def _collect_call_args_refs(self, node: ast.Call, module: str) -> None:
        for arg in node.args:
            if isinstance(arg, ast.Name):
                self._refs[module].add(arg.id)
            elif isinstance(arg, ast.Attribute):
                full = self._resolve_attr_path(arg)
                if full:
                    self._refs[module].add(full)
        for kw in node.keywords:
            if isinstance(kw.value, ast.Name):
                self._refs[module].add(kw.value.id)

    def _collect_assign_value_refs(self, node: ast.Assign, module: str) -> None:
        for target in node.targets:
            if isinstance(target, ast.Subscript) and isinstance(node.value, ast.Name):
                self._refs[module].add(node.value.id)

    def _resolve_attr_path(self, node: ast.Attribute) -> str | None:
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None

    # ── 死代码检测 ─────────────────────────────────────────

    def _find_dead_code(self) -> None:
        all_defs: dict[str, DefInfo] = {}
        for mod, defs in self._defs.items():
            for d in defs:
                fqn = f"{mod}.{d.name}"
                all_defs[fqn] = d

        # 收集所有引用名（含跨模块推测）
        referenced: set[str] = set()
        for mod, names in self._refs.items():
            for name in names:
                referenced.add(name)
                referenced.add(f"{mod}.{name}")
                if name in self._imports.get(mod, {}):
                    referenced.add(self._imports[mod][name])

        for fqn, d in all_defs.items():
            if self._is_exempt(d):
                continue
            if self._is_used(d, fqn, referenced):
                continue
            self._findings.append(Finding(
                file=d.file, line=d.line,
                category="dead_code", severity="medium",
                message=f"{d.kind} '{d.name}' 未被任何代码引用，可能是死代码",
            ))

    def _is_exempt(self, d: DefInfo) -> bool:
        if d.name in MAGIC_METHODS:
            return True
        if d.name.startswith("test_") or "tests." in d.file.replace("\\", "/"):
            return True
        if d.name == "main" and d.kind == "function":
            return True
        if d.kind == "method" and d.is_public:
            return True
        # 类内私有方法（通过 self._xxx 调用，AST 无法追踪）
        if d.kind == "method" and not d.is_public:
            return True
        return d.file.endswith("__init__.py") and d.is_public

    def _is_used(self, d: DefInfo, fqn: str, referenced: set[str]) -> bool:
        if fqn in referenced or d.name in referenced:
            return True
        mod = ".".join(fqn.split(".")[:-1])
        if d.name in self._refs.get(mod, set()):
            return True
        # 被 __init__.py re-export
        for imp_mod, imp_map in self._imports.items():
            if imp_mod.endswith("__init__"):
                for _, full_name in imp_map.items():
                    if full_name == fqn:
                        return True
        return False

    # ── 坏味道检测 ─────────────────────────────────────────

    def _find_bad_patterns(self, py_files: list[Path]) -> None:
        for fpath in py_files:
            self._current_module = self._file_to_module(fpath)
            try:
                source = fpath.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(fpath))
            except SyntaxError:
                continue
            self._check_file_patterns(tree, str(fpath))

    def _check_file_patterns(self, tree: ast.AST, filepath: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self._check_function(node, filepath)
            elif isinstance(node, ast.ExceptHandler):
                self._check_except(node, filepath)

    def _check_function(self, node: ast.FunctionDef, filepath: str) -> None:
        start = node.lineno
        end = node.end_lineno or start
        length = end - start + 1

        if length > MAX_FUNCTION_LINES:
            self._findings.append(Finding(
                file=filepath, line=start, category="style", severity="medium",
                message=f"函数 '{node.name}' 有 {length} 行，超过 {MAX_FUNCTION_LINES} 行限制",
            ))

        complexity = self._compute_complexity(node)
        if complexity > HIGH_COMPLEXITY_THRESHOLD:
            self._findings.append(Finding(
                file=filepath, line=start, category="complexity", severity="high",
                message=f"函数 '{node.name}' 圈复杂度 {complexity}，超过阈值 {HIGH_COMPLEXITY_THRESHOLD}",
            ))

        max_depth = self._compute_max_nesting(node)
        if max_depth > MAX_NESTING_DEPTH:
            self._findings.append(Finding(
                file=filepath, line=start, category="complexity", severity="medium",
                message=f"函数 '{node.name}' 最大嵌套深度 {max_depth}，超过阈值 {MAX_NESTING_DEPTH}",
            ))

        n_params = len(node.args.args)
        if n_params > MAX_PARAMETERS:
            self._findings.append(Finding(
                file=filepath, line=start, category="style", severity="low",
                message=f"函数 '{node.name}' 有 {n_params} 个参数，超过建议值 {MAX_PARAMETERS}",
            ))

        for default in node.args.defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self._findings.append(Finding(
                    file=filepath, line=node.lineno,
                    category="anti_pattern", severity="high",
                    message=f"函数 '{node.name}' 使用了可变默认参数",
                ))
                break

        if self._has_inconsistent_return(node):
            self._findings.append(Finding(
                file=filepath, line=start, category="anti_pattern", severity="low",
                message=f"函数 '{node.name}' 返回值不一致（混用 return expr / return / 无 return）",
            ))

        self._check_unreachable(node, filepath)

    def _check_except(self, node: ast.ExceptHandler, filepath: str) -> None:
        if node.type is None:
            self._findings.append(Finding(
                file=filepath, line=node.lineno,
                category="anti_pattern", severity="high",
                message="裸 except: 子句（会吞掉 KeyboardInterrupt 和 SystemExit）",
            ))
            return
        if isinstance(node.type, ast.Name) and node.type.id == "Exception":
            for stmt in node.body:
                if isinstance(stmt, ast.Pass):
                    self._findings.append(Finding(
                        file=filepath, line=node.lineno,
                        category="anti_pattern", severity="medium",
                        message="except Exception: pass — 吞掉了所有异常",
                    ))
                    break

    # ── 复杂度计算 ─────────────────────────────────────────

    def _compute_complexity(self, node: ast.AST) -> int:
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
            elif isinstance(child, ast.IfExp):
                complexity += 1
        return complexity

    def _compute_max_nesting(self, node: ast.AST) -> int:
        max_depth = 0
        current_depth = 0
        branching = (ast.If, ast.For, ast.While, ast.Try, ast.With)

        class DepthVisitor(ast.NodeVisitor):
            def generic_visit(self, n):
                nonlocal current_depth, max_depth
                if isinstance(n, branching):
                    current_depth += 1
                    max_depth = max(max_depth, current_depth)
                    super().generic_visit(n)
                    current_depth -= 1
                else:
                    super().generic_visit(n)

        DepthVisitor().visit(node)
        return max_depth

    def _has_inconsistent_return(self, node: ast.FunctionDef) -> bool:
        returns: list[bool] = []

        class ReturnVisitor(ast.NodeVisitor):
            def visit_Return(self, n):
                returns.append(n.value is not None)

        ReturnVisitor().visit(node)

        has_value = any(returns)
        has_bare = any(not r for r in returns)
        return has_value and has_bare

    def _check_unreachable(self, func_node: ast.FunctionDef, filepath: str) -> None:
        unreachable = (ast.Return, ast.Raise, ast.Break, ast.Continue)
        for i, stmt in enumerate(func_node.body):
            if isinstance(stmt, unreachable) and i < len(func_node.body) - 1:
                next_stmt = func_node.body[i + 1]
                self._findings.append(Finding(
                    file=filepath, line=next_stmt.lineno,
                    category="dead_code", severity="high",
                    message=f"不可达代码：{type(stmt).__name__.lower()} 之后的语句永远无法执行",
                ))

    # ── 统计 ──────────────────────────────────────────────

    def _compute_stats(self) -> dict:
        categories: dict[str, int] = defaultdict(int)
        severities: dict[str, int] = defaultdict(int)
        for f in self._findings:
            categories[f.category] += 1
            severities[f.severity] += 1

        total_funcs = sum(1 for defs in self._defs.values()
                          for d in defs if d.kind in ("function", "method"))
        total_classes = sum(1 for defs in self._defs.values()
                            for d in defs if d.kind == "class")

        return {
            "total_modules": len(self._defs),
            "total_functions": total_funcs,
            "total_classes": total_classes,
            "total_findings": len(self._findings),
            "by_category": dict(categories),
            "by_severity": dict(severities),
            "module_deps_count": sum(len(v) for v in self._module_deps.values()),
        }

    # ── 报告生成 ──────────────────────────────────────────

    def format_report(self, report: AnalysisReport) -> str:
        lines: list[str] = []
        lines.append("=" * 64)
        lines.append("  项目静态分析报告")
        lines.append("=" * 64)

        stats = report.stats
        lines.append(f"\n模块 {stats['total_modules']} | "
                     f"函数 {stats['total_functions']} | "
                     f"类 {stats['total_classes']} | "
                     f"发现 {stats['total_findings']}")

        if not report.findings:
            lines.append("\n未发现问题。")
            return "\n".join(lines)

        by_cat: dict[str, list[Finding]] = defaultdict(list)
        for f in report.findings:
            by_cat[f.category].append(f)

        labels = {
            "dead_code": "死代码",
            "complexity": "圈复杂度 / 嵌套深度",
            "style": "代码风格",
            "anti_pattern": "反模式",
            "syntax": "语法错误",
        }

        for cat, label in labels.items():
            items = by_cat.get(cat, [])
            if not items:
                continue
            lines.append(f"\n[{label}] ({len(items)} 项)")
            lines.append("-" * 48)
            for item in sorted(items, key=lambda x: (x.severity, x.file, x.line)):
                icon = {"high": "!!", "medium": "! ", "low": "~ "}.get(item.severity, "? ")
                lines.append(f"  {icon} {item.file}:{item.line}  {item.message}")

        lines.append(f"\n{'=' * 64}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="项目静态分析器")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--no-tests", action="store_true", help="不分析 tests/")
    parser.add_argument("--root", type=str, default=None, help="项目根目录")
    args = parser.parse_args()

    root = Path(args.root) if args.root else Path.cwd()
    analyzer = ProjectAnalyzer(root)
    report = analyzer.analyze(include_tests=not args.no_tests)

    if args.json:
        output = {
            "stats": report.stats,
            "findings": [
                {"file": f.file, "line": f.line, "category": f.category,
                 "severity": f.severity, "message": f.message}
                for f in report.findings
            ],
            "module_deps": {k: sorted(v) for k, v in report.module_deps.items()},
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(analyzer.format_report(report))

    high_count = sum(1 for f in report.findings if f.severity == "high")
    sys.exit(1 if high_count > 0 else 0)


if __name__ == "__main__":
    main()
