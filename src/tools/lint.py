"""项目规范检查与优化：扫描项目是否符合 AI Agent 开发规范，给出评分和优化建议。

基于 GitHub 2500+ 案例研究的"六大军规"：
  1. 命令 (Commands)  — 构建/测试/运行命令是否明确
  2. 测试 (Tests)     — 测试框架、覆盖率、测试文件
  3. 项目结构 (Structure) — 目录组织、模块划分
  4. 代码风格 (Style)  — linter/formatter、命名规范
  5. Git 工作流 (Git)  — 分支策略、提交规范、.gitignore
  6. 边界 (Boundaries) — AGENTS.md、三级边界系统
"""

import json
import re
from datetime import datetime
from pathlib import Path

# ── 工具定义 ──

TOOLS_LINT = [
    {
        "_meta": {"label": "规范检查", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "check_project",
            "description": "六维度规范扫描（命令/测试/结构/风格/Git/边界），0-100 评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前目录",
                    },
                    "fix": {
                        "type": "boolean",
                        "description": "自动修复可修复项，默认 False",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "生成规范", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "generate_agents_md",
            "description": "扫描项目生成/更新 AGENTS.md（构建/测试/风格/安全）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前目录",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "覆盖已有文件，默认 False（仅创建不存在时）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "文档同步检查", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "docs_sync_check",
            "description": (
                "检查当前 Git 工作区的文件变更是否违反了文档同步规范。"
                "遍历所有修改/新增/删除的文件，根据硬编码的映射表检查"
                "是否应该同步更新对应的文档。如果违反（改了代码但没改文档），"
                "给出明确的违规提醒。建议每次 commit 前运行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前项目目录",
                    },
                },
                "required": [],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# 检查规则定义
# ═══════════════════════════════════════════════════════════════

# 评级阈值
_SCORE_EXCELLENT = 85
_SCORE_GOOD = 70
_SCORE_FAIR = 50


# ── 维度 1：命令（满分 16 分）──
def _check_commands(root: Path) -> dict:
    """检查构建/测试/运行命令是否明确。"""
    issues = []
    suggestions = []
    score = 0
    max_score = 16

    has_makefile = (root / "Makefile").exists()
    has_package_json_scripts = False
    has_pyproject_scripts = False
    has_readme_commands = False
    has_agents_md_commands = False

    # 检查 package.json scripts
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            if pkg.get("scripts"):
                has_package_json_scripts = True
        except Exception:
            pass

    # 检查 pyproject.toml scripts
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            if "[project.scripts]" in text or "[tool.poetry.scripts]" in text:
                has_pyproject_scripts = True
        except Exception:
            pass

    # 检查 README 是否包含命令
    readme_files = list(root.glob("README*"))
    for rf in readme_files:
        try:
            content = rf.read_text(encoding="utf-8").lower()
            if any(kw in content for kw in ["npm ", "pip ", "pytest", "python ", "cargo ", "go "]):
                has_readme_commands = True
                break
        except Exception:
            pass

    # 检查 AGENTS.md 是否包含命令
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        try:
            content = agents_md.read_text(encoding="utf-8").lower()
            if "命令" in content or "commands" in content:
                has_agents_md_commands = True
        except Exception:
            pass

    # 评分
    if has_makefile:
        score += 5
        suggestions.append("Makefile 已定义构建入口，请确保包含 test/build/run 等常用目标")
    else:
        suggestions.append("建议添加 Makefile 统一管理构建、测试、运行命令")
        issues.append("缺少 Makefile：构建/测试命令散落各处")

    if has_package_json_scripts:
        score += 4
    if has_pyproject_scripts:
        score += 3

    if has_readme_commands:
        score += 4
    else:
        suggestions.append("建议在 README.md 中明确列出构建/测试/运行命令")
        issues.append("README.md 中未找到明确的命令行示例")

    if has_agents_md_commands:
        score += 4
    else:
        if agents_md.exists():
            issues.append("AGENTS.md 中未包含命令章节")
        suggestions.append("建议在 AGENTS.md 中添加「命令」章节，列出所有常用命令")

    return {
        "name": "命令 (Commands)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 维度 2：测试（满分 16 分）──
def _check_tests(root: Path) -> dict:
    """检查测试框架、测试文件、覆盖率。"""
    issues = []
    suggestions = []
    score = 0
    max_score = 16

    test_dirs = ["tests", "test", "__tests__", "spec", "specs"]
    has_test_dir = any((root / d).is_dir() for d in test_dirs)
    test_files_count = 0

    # 统计测试文件
    if has_test_dir:
        for d in test_dirs:
            td = root / d
            if td.is_dir():
                test_files_count += len(list(td.rglob("test_*.py")))
                test_files_count += len(list(td.rglob("*_test.py")))
                test_files_count += len(list(td.rglob("*.test.*")))

    # 检查 pytest 配置
    has_pytest_config = (
        (root / "pytest.ini").exists()
        or (
            (root / "pyproject.toml").exists()
            and "[tool.pytest" in (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        or ((root / "setup.cfg").exists() and "[tool:pytest" in (root / "setup.cfg").read_text(encoding="utf-8"))
    )

    # 检查 CI 中的测试
    has_ci_tests = False
    ci_dirs = [".github/workflows", ".gitlab-ci.yml", "Jenkinsfile"]
    for ci in ci_dirs:
        cip = root / ci
        if cip.exists():
            if cip.is_dir():
                for wf in cip.glob("*.yml"):
                    try:
                        if "test" in wf.read_text(encoding="utf-8").lower():
                            has_ci_tests = True
                            break
                    except Exception:
                        pass
            elif cip.is_file():
                try:
                    if "test" in cip.read_text(encoding="utf-8").lower():
                        has_ci_tests = True
                except Exception:
                    pass

    # 评分
    if has_test_dir:
        score += 6
        if test_files_count > 0:
            score += 3
        else:
            issues.append("测试目录存在但未找到测试文件")
            suggestions.append("在测试目录中添加测试用例")
    else:
        issues.append("缺少测试目录（tests/）")
        suggestions.append("创建 tests/ 目录并添加测试用例")

    if has_pytest_config:
        score += 3
    else:
        suggestions.append("添加 pytest.ini 或在 pyproject.toml 中配置 [tool.pytest]")

    if has_ci_tests:
        score += 4
    else:
        suggestions.append("建议在 CI（GitHub Actions 等）中添加自动测试流程")

    return {
        "name": "测试 (Tests)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 维度 3：项目结构（满分 18 分）──
def _check_structure(root: Path) -> dict:
    """检查目录组织、模块划分。"""
    issues = []
    suggestions = []
    score = 0
    max_score = 18

    # 检查核心目录
    has_src = (root / "src").is_dir()
    has_docs = (root / "docs").is_dir() or (root / "doc").is_dir()
    has_readme = any(root.glob("README*"))
    has_agents_md = (root / "AGENTS.md").exists()
    has_license = any(root.glob("LICENSE*"))
    has_gitignore = (root / ".gitignore").exists()

    # 检查 __init__.py 是否过大（超过 200 行使提示）
    init_files = list(root.rglob("__init__.py"))
    oversized_inits = [f for f in init_files if f.stat().st_size > 10000]

    # 检查是否有扁平化趋势（src 下超过 15 个 .py 文件）
    if has_src:
        src_files = list((root / "src").glob("*.py"))
        if len(src_files) > 15 and not (root / "src" / "tools").is_dir():
            issues.append(f"src/ 下有 {len(src_files)} 个 .py 文件，建议按功能分子目录")
            suggestions.append("将 src/ 下的文件按功能分组到子目录中")

    # 评分
    if has_src:
        score += 4
    else:
        issues.append("缺少 src/ 源码目录")
        suggestions.append("将源码放入 src/ 目录")

    if has_readme:
        score += 3
    else:
        issues.append("缺少 README.md")
        suggestions.append("创建 README.md 描述项目")

    if has_agents_md:
        score += 4
    else:
        issues.append("缺少 AGENTS.md（AI 智能体的项目操作手册）")
        suggestions.append("创建 AGENTS.md，定义构建命令、测试、代码风格、Git 工作流和安全边界")

    if has_docs:
        score += 2
    else:
        suggestions.append("建议添加 docs/ 目录存放详细文档")

    if has_license:
        score += 2

    if has_gitignore:
        score += 3
    else:
        issues.append("缺少 .gitignore")
        suggestions.append("添加 .gitignore 排除虚拟环境、缓存、构建产物等")

    if oversized_inits:
        names = [str(f.relative_to(root)) for f in oversized_inits]
        issues.append(f"__init__.py 过大（>{10000}B）: {', '.join(names)}")
        suggestions.append("将 __init__.py 中的逻辑拆分到独立模块，__init__.py 只做导入")

    return {
        "name": "项目结构 (Structure)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 维度 4：代码风格（满分 16 分）──
def _check_style(root: Path) -> dict:
    """检查 linter/formatter 配置、命名规范等。"""
    issues = []
    suggestions = []
    score = 0
    max_score = 16

    # Python linter 工具
    has_ruff = (root / "ruff.toml").exists() or (root / ".ruff.toml").exists()
    has_flake8 = (root / ".flake8").exists()
    has_black = (root / "pyproject.toml").exists() and "[tool.black]" in (
        (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").exists() else ""
    )
    has_pylint = (root / ".pylintrc").exists()
    has_precommit = (root / ".pre-commit-config.yaml").exists()

    # EditorConfig
    has_editorconfig = (root / ".editorconfig").exists()

    # 检查 pyproject.toml 中是否有代码风格配置
    pyproject_has_style = False
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            style_sections = ["[tool.ruff", "[tool.black", "[tool.isort", "[tool.mypy"]
            if any(s in text for s in style_sections):
                pyproject_has_style = True
        except Exception:
            pass

    if any([has_ruff, pyproject_has_style, has_flake8, has_pylint]):
        score += 5
        if has_ruff or pyproject_has_style:
            score += 2  # ruff 是现代标准
    else:
        issues.append("未配置 Python linter（ruff/flake8/pylint）")
        suggestions.append("添加 ruff 配置：pip install ruff && 在 pyproject.toml 中配置 [tool.ruff]")

    if any([has_black, pyproject_has_style]):
        score += 3
    else:
        suggestions.append("配置代码格式化工具（推荐 ruff format 或 black）")

    if has_precommit:
        score += 3
    else:
        suggestions.append("添加 .pre-commit-config.yaml 实现提交前自动检查")

    if has_editorconfig:
        score += 3
    else:
        suggestions.append("添加 .editorconfig 统一编辑器设置（缩进、换行符等）")

    return {
        "name": "代码风格 (Style)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 维度 5：Git 工作流（满分 16 分）──
def _check_git(root: Path) -> dict:
    """检查 Git 配置。"""
    import subprocess

    issues = []
    suggestions = []
    score = 0
    max_score = 16

    # 向上查找 .git（项目可能是 Git 子目录）
    def _find_git_dir(p: Path) -> Path | None:
        for parent in [p] + list(p.parents):
            if (parent / ".git").exists():
                return parent
        return None

    git_root = _find_git_dir(root)
    is_git_repo = git_root is not None

    if not is_git_repo:
        issues.append("不是 Git 仓库")
        suggestions.append("运行 git init 初始化版本控制")
        return {
            "name": "Git 工作流 (Git)",
            "score": 0,
            "max_score": max_score,
            "issues": issues,
            "suggestions": suggestions,
        }

    score += 3

    has_gitignore = (root / ".gitignore").exists()
    if has_gitignore:
        score += 3
    else:
        issues.append("缺少 .gitignore")
        suggestions.append("添加 .gitignore 排除 .env、__pycache__、.venv 等")

    # 检查 .gitignore 是否包含 .env
    if has_gitignore:
        try:
            gitignore = (root / ".gitignore").read_text(encoding="utf-8")
            if ".env" in gitignore:
                score += 2
            else:
                issues.append(".gitignore 未排除 .env 文件（有泄露 API 密钥风险）")
                suggestions.append("在 .gitignore 中添加 .env")
        except Exception:
            pass

    # 检查是否有未提交的变更
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10
        )
        dirty_files = len([line for line in result.stdout.split("\n") if line.strip()])
        if dirty_files > 10:
            issues.append(f"有 {dirty_files} 个未提交的文件变更，建议频繁小步提交")
            suggestions.append("将当前变更拆分为小的、有意义的提交")
        elif dirty_files > 0:
            score += 2  # 规模可控
        else:
            score += 4  # 干净
    except Exception:
        pass

    # 检查 git 提交历史
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "--oneline", "-1"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.stdout.strip():
            score += 2
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "-C", str(git_root or root), "remote", "-v"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10
        )
        if result.stdout.strip():
            score += 2
    except Exception:
        pass

    return {
        "name": "Git 工作流 (Git)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 维度 6：边界（满分 18 分）──
def _check_boundaries(root: Path) -> dict:
    """检查 AGENTS.md 中的三级边界系统、安全规则。"""
    issues = []
    suggestions = []
    score = 0
    max_score = 18

    agents_md = root / "AGENTS.md"
    if not agents_md.exists():
        issues.append("缺少 AGENTS.md（无法定义 AI 智能体的操作边界）")
        suggestions.append("创建 AGENTS.md 定义三级边界：✅始终执行 / ⚠️事先询问 / 🚫绝不执行")
        return {
            "name": "边界 (Boundaries)",
            "score": 0,
            "max_score": max_score,
            "issues": issues,
            "suggestions": suggestions,
        }

    score += 4
    try:
        content = agents_md.read_text(encoding="utf-8")

        # 检查是否包含三级边界
        has_always = any(kw in content for kw in ["始终执行", "Always", "always do", "✅"])
        has_ask = any(kw in content for kw in ["事先询问", "Ask First", "ask first", "⚠️", "⚠"])
        has_never = any(kw in content for kw in ["绝不执行", "Never", "never do", "🚫"])

        if has_always and has_ask and has_never:
            score += 7
        elif has_always or has_ask or has_never:
            score += 3
            issues.append("AGENTS.md 的边界定义不完整，需要三级边界：✅/⚠️/🚫")
            suggestions.append("完善 AGENTS.md 中的三级边界系统")
        else:
            issues.append("AGENTS.md 缺少边界定义")
            suggestions.append("在 AGENTS.md 中添加三级边界：✅始终 / ⚠️询问 / 🚫禁止")

        # 检查是否包含 "严禁提交密钥" 相关内容
        if "密钥" in content or "secret" in content.lower() or "api_key" in content.lower():
            score += 4
        else:
            issues.append("AGENTS.md 未声明密钥/凭证保护规则")
            suggestions.append("在 AGENTS.md 中明确声明：🚫严禁提交密钥和 API 凭证")

        # 检查是否包含安全相关章节
        if "安全" in content or "security" in content.lower():
            score += 3
        else:
            suggestions.append("在 AGENTS.md 中添加安全章节")

    except Exception:
        pass

    return {
        "name": "边界 (Boundaries)",
        "score": min(score, max_score),
        "max_score": max_score,
        "issues": issues,
        "suggestions": suggestions,
    }


# ── 额外加分项 ──
def _check_bonus(root: Path) -> dict:
    """额外加分/减分项。"""
    items = []

    # 加分
    if (root / "docs" / "roadmap.md").exists():
        items.append(("+", "有 docs/roadmap.md，项目规划清晰"))
    if (root / "docs" / "changelog.md").exists():
        items.append(("+", "有 docs/changelog.md，版本变更可追溯"))
    if (root / "docs" / "contributing.md").exists():
        items.append(("+", "有 docs/contributing.md，贡献指南完善"))
    if (root / ".github" / "workflows").is_dir():
        items.append(("+", "有 GitHub Actions CI/CD 配置"))

    # 减分
    if (root / "node_modules").is_dir():
        items.append(("-", "node_modules/ 未被 .gitignore 排除（或误留在项目根目录）"))
    if list(root.glob("*.pyc")):
        items.append(("-", "存在 .pyc 编译产物，建议清理并确保已 gitignore"))

    return {"items": items}


# ═══════════════════════════════════════════════════════════════
# 主检查函数
# ═══════════════════════════════════════════════════════════════

def _get_rating(score: int) -> str:
    if score >= _SCORE_EXCELLENT:
        return "🏆 优秀"
    elif score >= _SCORE_GOOD:
        return "👍 良好"
    elif score >= _SCORE_FAIR:
        return "⚠️ 一般"
    else:
        return "🔴 需改进"


def check_project(path: str | None = None, fix: bool = False) -> str:
    """全面检查项目规范。"""
    root = Path(path).resolve() if path else Path.cwd()

    if not root.is_dir():
        return f"目录不存在: {root}"

    lines = []
    lines.append("╔══════════════════════════════════════════╗")
    lines.append("║    🔍 AI Agent 项目规范检查报告          ║")
    lines.append("╚══════════════════════════════════════════╝")
    lines.append("")
    lines.append(f"📂 项目: {root.name}")
    lines.append(f"📍 路径: {root}")
    lines.append(f"🕐 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 六大维度检查
    dimensions = [
        _check_commands(root),
        _check_tests(root),
        _check_structure(root),
        _check_style(root),
        _check_git(root),
        _check_boundaries(root),
    ]

    total_score = 0
    total_max = 0

    lines.append("── 维度评分 ──")
    lines.append("")

    for dim in dimensions:
        pct = dim["score"] / dim["max_score"] * 100
        bar_len = 12
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        lines.append(f"  {dim['name']}")
        lines.append(f"    [{bar}] {dim['score']}/{dim['max_score']} ({pct:.0f}%)")
        total_score += dim["score"]
        total_max += dim["max_score"]

    # 额外加减分
    bonus = _check_bonus(root)
    bonus_score = sum(2 if b[0] == "+" else -2 for b in bonus["items"])
    total_score = max(0, total_score + bonus_score)

    lines.append("")
    overall_pct = total_score / total_max * 100
    rating = _get_rating(total_score)
    lines.append(f"  📊 总分: {total_score}/{total_max} ({overall_pct:.0f}%) → {rating}")
    if bonus["items"]:
        lines.append(f"     （含额外加减分: {bonus_score:+d}）")
    lines.append("")

    # 问题汇总
    all_issues = []
    all_suggestions = []
    for dim in dimensions:
        for issue in dim["issues"]:
            all_issues.append((dim["name"].split("(")[0].strip(), issue))
        for sug in dim["suggestions"]:
            all_suggestions.append((dim["name"].split("(")[0].strip(), sug))

    if all_issues:
        lines.append("── 🔴 发现问题 ──")
        lines.append("")
        for dim_name, issue in all_issues:
            lines.append(f"  [{dim_name}] {issue}")
        lines.append("")

    if all_suggestions:
        lines.append("── 💡 优化建议 ──")
        lines.append("")
        for dim_name, sug in all_suggestions:
            lines.append(f"  [{dim_name}] {sug}")
        lines.append("")

    # 额外项
    if bonus["items"]:
        lines.append("── 📝 其他发现 ──")
        lines.append("")
        for tag, item in bonus["items"]:
            lines.append(f"  {tag} {item}")
        lines.append("")

    # 自动修复
    if fix:
        lines.append("── 🔧 自动修复 ──")
        lines.append("")
        agents_md = root / "AGENTS.md"
        if not agents_md.exists():
            try:
                _generate_agents_md_content(root, agents_md)
                lines.append("  ✅ 已生成 AGENTS.md")
            except Exception as e:
                lines.append(f"  ❌ 生成 AGENTS.md 失败: {e}")
        else:
            lines.append("  ℹ️  AGENTS.md 已存在，跳过生成")
        lines.append("")

    # 总结
    lines.append("── 📋 总结 ──")
    lines.append("")
    if overall_pct >= _SCORE_EXCELLENT:
        lines.append("  项目规范良好，继续保持！")
    elif overall_pct >= _SCORE_GOOD:
        lines.append("  项目基础不错，重点优化上述建议的问题。")
    elif overall_pct >= _SCORE_FAIR:
        lines.append("  项目有多项需要改进，建议优先处理 🔴 问题列表。")
    else:
        lines.append(
            "  项目规范严重不足，建议参考 AGENTS.md 规范指南"
            "（yeasy.gitbook.io/agentic_ai_guide）进行全面整改。"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# AGENTS.md 自动生成
# ═══════════════════════════════════════════════════════════════

def _detect_tech_stack(root: Path) -> list[str]:
    """检测项目技术栈。"""
    stack = []

    # Python
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or (root / "setup.py").exists():
        py_ver = "3.11+"
        try:
            pp = root / "pyproject.toml"
            if pp.exists():
                text = pp.read_text(encoding="utf-8")
                m = re.search(r'requires-python\s*=\s*[">=]*([\d.]+)', text)
                if m:
                    py_ver = m.group(1) + "+"
        except Exception:
            pass
        stack.append(f"Python {py_ver}")

    # Node.js
    if (root / "package.json").exists():
        stack.append("Node.js")
    # Rust
    if (root / "Cargo.toml").exists():
        stack.append("Rust")
    # Go
    if (root / "go.mod").exists():
        stack.append("Go")

    return stack


def _detect_test_commands(root: Path) -> list[str]:
    """检测测试命令。"""
    cmds = []

    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        cmds.append("pytest")
    if (root / "package.json").exists():
        cmds.append("npm test")
    if (root / "Cargo.toml").exists():
        cmds.append("cargo test")
    if (root / "go.mod").exists():
        cmds.append("go test ./...")

    return cmds


def _detect_build_commands(root: Path) -> list[str]:
    """检测构建命令。"""
    cmds = []

    if (root / "pyproject.toml").exists():
        cmds.append("pip install -e .")
    if (root / "requirements.txt").exists():
        cmds.append("pip install -r requirements.txt")
    if (root / "package.json").exists():
        cmds.append("npm install")
    if (root / "Cargo.toml").exists():
        cmds.append("cargo build")

    return cmds


def _build_file_tree(root: Path, depth: int = 2, prefix: str = "") -> str:
    """构建简化的文件树。"""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return ""

    exclude = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache",
               ".mypy_cache", ".ruff_cache", "dist", "build", ".idea", ".vscode",
               "sessions", "plugins", ".agent_backups"}

    entries = [e for e in entries if e.name not in exclude]

    for i, entry in enumerate(entries[:25]):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        if entry.is_dir():
            lines.append(f"{prefix}{connector}{entry.name}/")
            if depth > 1:
                sub = _build_file_tree(entry, depth - 1, prefix + ("    " if is_last else "│   "))
                lines.append(sub)
        else:
            lines.append(f"{prefix}{connector}{entry.name}")

    return "\n".join(lines)


def _generate_agents_md_content(root: Path, output_path: Path) -> None:
    """生成 AGENTS.md 内容并写入。"""
    stack = _detect_tech_stack(root)
    test_cmds = _detect_test_commands(root)
    build_cmds = _detect_build_commands(root)
    tree = _build_file_tree(root)

    lines = []
    lines.append("# AGENTS.md — AI 编程智能体操作手册")
    lines.append("")
    lines.append("> 本文档面向在此项目中工作的 AI 编程智能体（如 Claude Code、Cline、Copilot 等）。")
    lines.append("> 人类开发者请阅读 README.md。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 技术栈
    if stack:
        lines.append("## 技术栈")
        lines.append("")
        for s in stack:
            lines.append(f"- {s}")
        lines.append("")

    # 命令
    lines.append("## 命令")
    lines.append("")
    lines.append("| 命令 | 用途 |")
    lines.append("|------|------|")
    for cmd in build_cmds:
        lines.append(f"| `{cmd}` | 构建/安装依赖 |")
    for cmd in test_cmds:
        lines.append(f"| `{cmd}` | 运行测试 |")
    if (root / "Makefile").exists():
        lines.append("| `make` | 查看所有可用目标 |")
    lines.append("")

    # 项目结构
    lines.append("## 项目结构")
    lines.append("")
    lines.append("```")
    lines.append(tree)
    lines.append("```")
    lines.append("")

    # 代码风格
    lines.append("## 代码风格")
    lines.append("")
    if "Python" in " ".join(stack):
        lines.append("- 命名: snake_case（函数/变量），PascalCase（类），UPPER_SNAKE（常量）")
        lines.append("- 类型注解: 公共函数必须有")
        lines.append("- 文档字符串: 模块和公共函数必须有")
        lines.append("- 格式化: 使用 ruff 或 black")
        lines.append("")
    if "JavaScript" in " ".join(stack) or "TypeScript" in " ".join(stack):
        lines.append("- 使用 Prettier 格式化，ESLint 检查")
        lines.append("")

    # Git 工作流
    lines.append("## Git 工作流")
    lines.append("")
    lines.append("- 分支命名: `feat/描述`、`fix/描述`、`refactor/描述`")
    lines.append("- 提交前: 运行测试确保不破坏现有功能")
    lines.append("- 提交信息: 简洁描述变更内容")
    lines.append("")

    # 边界
    lines.append("## 边界")
    lines.append("")
    lines.append("### ✅ 始终执行（Always）")
    lines.append("- 读取文件、搜索代码、查看 Git 状态")
    lines.append("- 运行测试")
    lines.append("- 读取项目配置文件")
    lines.append("")
    lines.append("### ⚠️ 事先询问（Ask First）")
    lines.append("- 修改核心模块")
    lines.append("- 添加新依赖")
    lines.append("- 执行 `git push` 到远程仓库")
    lines.append("- 修改配置文件")
    lines.append("")
    lines.append("### 🚫 绝不执行（Never）")
    lines.append("- 提交 `.env` 文件或 API 密钥")
    lines.append("- 执行 `git push --force` 到 main/master")
    lines.append("- 删除 `.git` 目录")
    lines.append("")

    # 安全
    lines.append("## 安全")
    lines.append("")
    lines.append("- API 密钥通过 `.env` 文件管理，绝不硬编码")
    lines.append("- `.env` 必须在 `.gitignore` 中")
    lines.append("- 文件写入前自动备份")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_agents_md(path: str | None = None, overwrite: bool = False) -> str:
    """自动生成或更新 AGENTS.md。"""
    root = Path(path).resolve() if path else Path.cwd()

    if not root.is_dir():
        return f"目录不存在: {root}"

    agents_md = root / "AGENTS.md"

    if agents_md.exists() and not overwrite:
        return (
            f"AGENTS.md 已存在: {agents_md}\n\n"
            f"如需覆盖，请设置 overwrite=True。\n"
            f"或使用 check_project(fix=True) 来检查并修复其他规范问题。"
        )

    try:
        _generate_agents_md_content(root, agents_md)
        action = "已更新" if agents_md.exists() else "已生成"
        return f"{action} AGENTS.md: {agents_md}\n\n请检查并根据项目实际情况调整内容。"
    except Exception as e:
        return f"生成 AGENTS.md 失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 文档同步检查
# ═══════════════════════════════════════════════════════════════

# 硬编码的"改代码 → 必须更新哪些文档"映射表
# 新增/修改模块后必须同步对应的文档，否则检查不通过
_DOC_SYNC_MAP: dict[str, list[str]] = {
    # 核心模块变更 → 影响哪些文档
    "src/llm.py":          ["AGENTS.md", "docs/architecture.md"],
    "src/commands.py":     ["AGENTS.md", "README.md"],
    "src/main.py":         ["README.md", "docs/architecture.md"],
    "src/config.py":       [".env.example", "README.md"],
    "src/session.py":      ["AGENTS.md", "docs/architecture.md"],

    # 工具模块变更 → 必须更新对应文档
    "src/tools/lint.py":       ["AGENTS.md", "docs/features.md", "docs/roadmap.md"],
    "src/tools/git.py":        ["AGENTS.md"],
    "src/tools/project.py":    ["AGENTS.md"],
    "src/tools/web.py":        ["AGENTS.md"],
    "src/tools/memory.py":     ["AGENTS.md"],
    "src/tools/code.py":       ["AGENTS.md"],
    "src/tools/think.py":      ["AGENTS.md"],
    "src/tools/tokens.py":     ["AGENTS.md"],

    # 测试变更 → 至少更新 CHANGELOG
    "tests/":                  ["docs/changelog.md"],
    "tests/test_cache.py":     ["docs/cache-design.md"],

    # 配置变更 → 影响范围大
    "pyproject.toml":          ["README.md", "AGENTS.md", "docs/changelog.md"],
    "Makefile":                ["README.md", "AGENTS.md"],
    ".github/workflows/":      ["README.md", "AGENTS.md"],

    # 文档互相引用
    "AGENTS.md":               ["README.md"],
    "README.md":               ["AGENTS.md"],
    "docs/roadmap.md":         ["README.md"],
    "docs/features.md":        ["docs/changelog.md"],
    "docs/cache-design.md":    ["AGENTS.md", "docs/architecture.md"],
}

# 白名单：修改以下文件 / 目录不需要同步文档
_DOC_SYNC_IGNORE = {
    ".gitignore", ".editorconfig",
    "__pycache__", ".ruff_cache", ".pytest_cache",
    ".env", ".env.local",
    "sessions/", "plugins/", "memory/", ".agent_backups/",
    ".pre-commit-config.yaml",
}


def _classify_changed_file(changed: str) -> str:
    """将变更文件归类到 _DOC_SYNC_MAP 中的 key。"""
    changed = changed.replace("\\", "/")

    # 精确匹配
    if changed in _DOC_SYNC_MAP:
        return changed

    # 前缀匹配（如 tests/xxx → tests/）
    for key in _DOC_SYNC_MAP:
        if key.endswith("/") and changed.startswith(key):
            return key

    # tools/ 目录下任意文件
    if changed.startswith("src/tools/"):
        return changed  # 精确返回，不走默认

    # 不在映射表中 → 不需要强制同步
    return ""


def docs_sync_check(path: str | None = None) -> str:
    """检查当前变更是否违反了文档同步规范。

    遍历所有修改/新增/删除的文件，检查是否应该更新对应的文档。
    如果违反（改了代码但没改文档），给出明确提醒。
    """
    import subprocess

    root = Path(path).resolve() if path else Path.cwd()

    # 获取变更文件列表
    changed_files: list[str] = []
    try:
        # 未暂存 + 已暂存
        for cmd_flag in ["--name-only", "--cached --name-only"]:
            r = subprocess.run(
                f"git -C {root} diff {cmd_flag}".split(),
                capture_output=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split("\n"):
                    line = line.strip()
                    if line:
                        changed_files.append(line)

        # 未跟踪文件
        r = subprocess.run(
            f"git -C {root} status --porcelain".split(),
            capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("?? "):
                    changed_files.append(line[3:].strip())
    except Exception:
        return "无法获取 Git 变更（可能不是 Git 仓库）"

    if not changed_files:
        return "✅ 没有检测到变更"

    # 去重
    changed_files = sorted(set(changed_files))

    lines = []
    lines.append("📋 文档同步检查")
    lines.append("")
    lines.append(f"检测到 {len(changed_files)} 个变更文件：")

    has_violations = False

    for f in changed_files:
        # 跳过忽略的文件
        f_norm = f.replace("\\", "/")
        if any(ig in f_norm for ig in _DOC_SYNC_IGNORE):
            continue

        key = _classify_changed_file(f_norm)
        if not key:
            continue

        # 获取该文件的文档依赖
        if key in _DOC_SYNC_MAP:
            required_docs = _DOC_SYNC_MAP[key]
        elif key.endswith("/"):
            required_docs = _DOC_SYNC_MAP.get(key, [])
        else:
            required_docs = []

        if not required_docs:
            continue

        # 检查这些文档是否也在变更中（说明被同步更新了）
        changed_norm = {c.replace("\\", "/") for c in changed_files}
        missing = [d for d in required_docs if d not in changed_norm]

        f_short = f_norm
        if len(f_short) > 50:
            f_short = "..." + f_short[-47:]

        if missing:
            has_violations = True
            lines.append(f"  🔴 {f_short}")
            for m in missing:
                lines.append(f"       ⚠️ 必须同步更新: {m}")
        else:
            lines.append(f"  ✅ {f_short}")

    lines.append("")

    if has_violations:
        lines.append("🔴 文档同步违规！以下规则未遵守：")
        lines.append("   改代码 → 必须同步更新对应的文档文件")
        lines.append("   请修改上述标记 ⚠️ 的文档文件后再提交。")
        lines.append("")
        lines.append("📖 文档同步映射关系定义在: src/tools/lint.py _DOC_SYNC_MAP")
    else:
        lines.append("✅ 所有变更都已同步对应文档，合规！")

    return "\n".join(lines)


# ── 调度 ──
def execute(name: str, args: dict) -> str | None:
    """根据工具名分发到对应的检查函数。"""
    if name == "check_project":
        return check_project(
            path=args.get("path"),
            fix=args.get("fix", False),
        )
    if name == "generate_agents_md":
        return generate_agents_md(
            path=args.get("path"),
            overwrite=args.get("overwrite", False),
        )
    if name == "docs_sync_check":
        return docs_sync_check(
            path=args.get("path"),
        )
    return None
