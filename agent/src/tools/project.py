"""项目结构感知：启动时自动分析项目信息，注入 system prompt。"""

import os
import platform
import subprocess
from pathlib import Path


# ── 项目类型检测 ──

_PROJECT_INDICATORS = {
    "Python": [
        "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
        "Pipfile", "poetry.lock", "Pipfile.lock",
    ],
    "Node.js": [
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "tsconfig.json", ".npmrc",
    ],
    "Rust": ["Cargo.toml", "Cargo.lock"],
    "Go": ["go.mod", "go.sum"],
    "Java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "C/C++": ["CMakeLists.txt", "Makefile", "configure"],
    "Ruby": ["Gemfile", "Gemfile.lock"],
    "PHP": ["composer.json", "composer.lock"],
    ".NET": ["*.csproj", "*.sln"],
}


def _detect_project_type(project_dir: Path) -> str:
    """检测项目类型。返回类型名称，如 'Python'、'Node.js'。"""
    scores: dict[str, int] = {}
    for proj_type, indicators in _PROJECT_INDICATORS.items():
        for ind in indicators:
            if ind.startswith("*"):
                # 通配符匹配
                if list(project_dir.glob(ind)):
                    scores[proj_type] = scores.get(proj_type, 0) + 2
            elif (project_dir / ind).exists():
                scores[proj_type] = scores.get(proj_type, 0) + 1

    if not scores:
        return "未知"

    # 返回得分最高的
    return max(scores, key=scores.get)


def _detect_build_tool(project_dir: Path) -> str | None:
    """检测构建/包管理工具。"""
    checks = [
        ("pyproject.toml", "pip/pdm/poetry"),
        ("requirements.txt", "pip"),
        ("package.json", "npm/yarn"),
        ("Cargo.toml", "cargo"),
        ("go.mod", "go"),
        ("Makefile", "make"),
        ("Gemfile", "bundler"),
    ]
    for filename, tool in checks:
        if (project_dir / filename).exists():
            return tool
    return None


def _detect_test_command(project_dir: Path) -> str | None:
    """检测测试命令。"""
    checks = [
        ("pyproject.toml", "pytest"),
        ("pytest.ini", "pytest"),
        ("setup.cfg", "pytest"),
        ("package.json", "npm test"),
        ("Cargo.toml", "cargo test"),
        ("go.mod", "go test"),
        ("Gemfile", "bundle exec rspec"),
        ("Makefile", "make test"),
    ]
    for filename, cmd in checks:
        if (project_dir / filename).exists():
            return cmd
    return None


def _detect_git_info(project_dir: Path) -> dict:
    """检测 Git 信息。"""
    info = {"available": False, "branch": "", "has_changes": False}
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=3, cwd=project_dir,
        )
        if r.returncode != 0 or r.stdout.strip() != "true":
            return info
        info["available"] = True

        # 分支名
        r2 = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=project_dir,
        )
        if r2.returncode == 0:
            info["branch"] = r2.stdout.strip()

        # 是否有未提交变更
        r3 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=3, cwd=project_dir,
        )
        if r3.returncode == 0 and r3.stdout.strip():
            info["has_changes"] = True

    except Exception:
        pass
    return info


def _scan_key_dirs(project_dir: Path) -> list[dict]:
    """扫描关键目录的结构。"""
    key_dirs = ["src", "source", "lib", "app", "tests", "test", "docs", "scripts", "tools"]
    result = []
    for d in key_dirs:
        dp = project_dir / d
        if dp.is_dir():
            # 统计文件数（排除 __pycache__、.git 等）
            file_count = 0
            for f in dp.rglob("*"):
                if f.is_file() and ".pyc" not in f.suffix and "__pycache__" not in str(f):
                    file_count += 1
            result.append({
                "path": d,
                "files": file_count,
            })
    return result


def _detect_python_version() -> str:
    """检测 Python 版本。"""
    try:
        return f"Python {platform.python_version()}"
    except Exception:
        return ""


def _detect_node_version(project_dir: Path) -> str:
    """检测 Node.js 版本。"""
    try:
        r = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return f"Node.js {r.stdout.strip()}"
    except Exception:
        pass
    return ""


# ── 关键文件速查 ──

_IMPORTANT_FILES = [
    "README.md", "ROADMAP.md", "FEATURES.md", "CHANGELOG.md",
    ".env.example", "docker-compose.yml", "Dockerfile",
    ".gitignore", ".gitattributes",
]


def _find_important_files(project_dir: Path) -> list[str]:
    """找到项目中的重要文档和配置。"""
    found = []
    for f in _IMPORTANT_FILES:
        if (project_dir / f).exists():
            found.append(f)
    return found


# ── 主函数 ──


def scan_project(project_dir: str | Path | None = None) -> str:
    """扫描项目目录，生成结构感知摘要。
    返回格式化的 Markdown 文本，可直接注入 system prompt。
    """
    root = Path(project_dir).expanduser().resolve() if project_dir else Path.cwd()
    if not root.exists():
        return ""

    lines: list[str] = []
    lines.append("📁 项目结构感知")
    lines.append("")

    # 1. 项目类型
    proj_type = _detect_project_type(root)
    build_tool = _detect_build_tool(root)
    test_cmd = _detect_test_command(root)
    lines.append(f"项目类型: {proj_type}")
    if build_tool:
        lines.append(f"构建工具: {build_tool}")
    if test_cmd:
        lines.append(f"测试命令: {test_cmd}")

    # 2. 运行时版本
    versions = []
    if proj_type == "Python":
        ver = _detect_python_version()
        if ver:
            versions.append(ver)
    elif proj_type == "Node.js":
        ver = _detect_node_version(root)
        if ver:
            versions.append(ver)
    if versions:
        lines.append(f"运行时: {'、'.join(versions)}")

    # 3. Git 信息
    git_info = _detect_git_info(root)
    if git_info["available"]:
        branch_str = f"分支: {git_info['branch']}"
        changes_str = "有未提交变更" if git_info["has_changes"] else "工作区干净"
        lines.append(f"Git: {branch_str}（{changes_str}）")

    # 4. 关键目录
    dirs = _scan_key_dirs(root)
    if dirs:
        lines.append("关键目录:")
        for d in dirs:
            lines.append(f"  {d['path']}/  — {d['files']} 个文件")

    # 5. 重要文档
    docs = _find_important_files(root)
    if docs:
        lines.append(f"关键文档: {'、'.join(docs)}")

    # 6. 项目自述（README 首段）
    readme_path = root / "README.md"
    if readme_path.exists():
        try:
            text = readme_path.read_text(encoding="utf-8")
            # 取第一段正文（去掉标题行）
            first_para = ""
            for line in text.split("\n"):
                if line.strip() and not line.startswith("#"):
                    first_para = line.strip()[:120]
                    break
            if first_para:
                lines.append(f"项目简介: {first_para}")
        except Exception:
            pass

    lines.append("")
    lines.append("以上信息自动感知，可直接用于决策（如选择构建命令、定位源码等）。")
    lines.append("如果感知信息不准确，可以通过对话告知更正。")

    return "\n".join(lines)
