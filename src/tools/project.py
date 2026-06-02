"""项目结构感知：自动扫描项目、识别语言/框架、分析依赖、生成结构树。"""

import json
import re
import time
from pathlib import Path

# ── 工具定义 ──

TOOLS_PROJECT = [
    {
        "type": "function",
        "function": {
            "name": "scan_project",
            "description": (
                "扫描项目目录，分析项目结构、语言、框架、依赖和构建命令。"
                "返回项目概览，包括文件树、检测到的技术栈、可用的构建/测试命令。"
                "适合新项目首次分析或需要了解项目整体结构时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "项目路径，默认当前项目目录",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "文件树显示深度，默认 2，最大 4",
                    },
                    "include_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "额外包含的文件模式，如 ['*.rs', '*.go']。默认会自动检测常见源码文件",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── 检测配置 ──

# 语言/框架检测规则：检测文件 → (语言, 框架, 构建命令)
_DETECT_RULES: list[tuple[str, str, str, list[str]]] = [
    # Python
    ("pyproject.toml",       "Python",    "pdm/poetry/setuptools", ["pytest", "python -m pytest", "poetry run pytest"]),
    ("requirements.txt",     "Python",    "pip",                   ["pytest", "python -m pytest"]),
    ("Pipfile",              "Python",    "pipenv",                ["pytest", "python -m pytest"]),
    ("setup.py",             "Python",    "setuptools",            ["pytest", "python -m pytest"]),
    ("setup.cfg",            "Python",    "setuptools",            ["pytest", "python -m pytest"]),
    # Node.js
    ("package.json",         "JavaScript/TypeScript", "npm/yarn/pnpm", ["npm test", "npm run test", "yarn test"]),
    ("yarn.lock",            "JavaScript/TypeScript", "yarn",            ["yarn test"]),
    ("pnpm-lock.yaml",       "JavaScript/TypeScript", "pnpm",            ["pnpm test"]),
    # Rust
    ("Cargo.toml",           "Rust",      "cargo",                 ["cargo test", "cargo build"]),
    # Go
    ("go.mod",               "Go",        "go modules",            ["go test ./...", "go build ./..."]),
    # Java
    ("pom.xml",              "Java",      "Maven",                 ["mvn test", "mvn package"]),
    ("build.gradle",         "Java",      "Gradle",                ["./gradlew test", "gradle test"]),
    ("build.gradle.kts",     "Java/Kotlin", "Gradle Kotlin DSL",   ["./gradlew test"]),
    # C/C++
    ("CMakeLists.txt",       "C/C++",     "CMake",                 ["cmake --build .", "ctest"]),
    ("Makefile",             "C/C++",     "Make",                  ["make", "make test"]),
    # Ruby
    ("Gemfile",              "Ruby",      "bundler",               ["bundle exec rspec", "rake test"]),
    # .NET
    ("*.csproj",             "C#",        ".NET",                  ["dotnet test", "dotnet build"]),
    ("*.sln",                "C#",        ".NET Solution",         ["dotnet test"]),
    # Swift
    ("Package.swift",        "Swift",     "SPM",                   ["swift test"]),
    # Docker
    ("Dockerfile",           "-",         "Docker",                []),
    ("docker-compose.yml",   "-",         "Docker Compose",        []),
    ("docker-compose.yaml",  "-",         "Docker Compose",        []),
]

# 排除的目录
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", ".tox", ".eggs", "dist", "build", ".next", ".nuxt",
    "target", "bin", "obj", "vendor", ".bundle", ".sass-cache",
    ".agent_backups", "sessions", "plugins", ".idea", ".vscode",
    "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

# 二进制/非文本文件扩展名
_BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".o", ".a", ".lib",
    ".class", ".jar", ".war", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".7z", ".rar", ".mp3", ".mp4", ".avi", ".mov",
    ".db", ".sqlite", ".sqlite3", ".lock",
}

# 感兴趣的源码扩展名
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".clj", ".cljs",
    ".rs", ".go", ".rb", ".php", ".swift", ".c", ".cpp", ".cxx",
    ".h", ".hpp", ".hxx", ".cs", ".fs", ".fsx",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".yaml", ".yml", ".json", ".xml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".mdx", ".rst", ".txt",
    ".sql", ".graphql", ".proto",
    ".vue", ".svelte", ".astro",
    ".css", ".scss", ".sass", ".less", ".styl",
    ".html", ".htm", ".xhtml",
    ".dockerfile", ".env",
    ".gradle", ".gradle.kts",
    ".cmake", ".mk",
    ".pl", ".pm", ".t",
    ".lua", ".r", ".m", ".mm",
}

# 构建/测试命令关键词
_BUILD_KEYWORDS = ["build", "compile", "package", "dist"]
_TEST_KEYWORDS = ["test", "spec", "e2e", "integration", "check", "lint"]
_RUN_KEYWORDS = ["start", "dev", "run", "serve"]


# ── 核心函数 ──


def _detect_language_and_framework(root: Path) -> dict:
    """检测项目的语言、框架、构建命令。"""
    result = {
        "languages": [],
        "frameworks": [],
        "build_commands": [],
        "test_commands": [],
        "run_commands": [],
        "config_files": [],
        "dependencies": [],
    }

    # 检测规则匹配
    for pattern, language, framework, test_cmds in _DETECT_RULES:
        if "*" in pattern:
            # 通配符匹配
            matches = list(root.glob(pattern))
            if matches:
                result["config_files"].append(str(matches[0].relative_to(root)))
                if language != "-" and language not in result["languages"]:
                    result["languages"].append(language)
                if framework and framework not in result["frameworks"]:
                    result["frameworks"].append(framework)
                for cmd in test_cmds:
                    if cmd not in result["test_commands"]:
                        result["test_commands"].append(cmd)
        else:
            config_file = root / pattern
            if config_file.exists():
                result["config_files"].append(pattern)
                if language != "-" and language not in result["languages"]:
                    result["languages"].append(language)
                if framework and framework not in result["frameworks"]:
                    result["frameworks"].append(framework)
                for cmd in test_cmds:
                    if cmd not in result["test_commands"]:
                        result["test_commands"].append(cmd)

    # 从源码文件推断语言
    if not result["languages"]:
        ext_count: dict[str, int] = {}
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in _SOURCE_EXTENSIONS:
                skip = False
                for part in f.parts:
                    if part in _EXCLUDE_DIRS or part.startswith("."):
                        skip = True
                        break
                if skip:
                    continue
                ext_count[f.suffix] = ext_count.get(f.suffix, 0) + 1

        # 按扩展名映射语言
        ext_to_lang = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".jsx": "JavaScript", ".tsx": "TypeScript",
            ".java": "Java", ".rs": "Rust", ".go": "Go",
            ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
            ".c": "C", ".cpp": "C++", ".cs": "C#",
            ".sh": "Shell", ".vue": "Vue", ".svelte": "Svelte",
            ".css": "CSS", ".scss": "SCSS", ".html": "HTML",
        }
        lang_count: dict[str, int] = {}
        for ext, count in ext_count.items():
            lang = ext_to_lang.get(ext)
            if lang:
                lang_count[lang] = lang_count.get(lang, 0) + count

        # 取前 3 多的语言
        sorted_langs = sorted(lang_count.items(), key=lambda x: -x[1])
        for lang, _ in sorted_langs[:3]:
            result["languages"].append(lang)

    # 从 package.json 读取 scripts
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            for name, cmd in scripts.items():
                has_build = any(kw in name.lower() for kw in _BUILD_KEYWORDS)
                has_test = any(kw in name.lower() for kw in _TEST_KEYWORDS)
                has_run = any(kw in name.lower() for kw in _RUN_KEYWORDS)
                full_cmd = f"npm run {name}"
                if has_test and full_cmd not in result["test_commands"]:
                    result["test_commands"].append(full_cmd)
                if has_build and full_cmd not in result["build_commands"]:
                    result["build_commands"].append(full_cmd)
                if has_run and full_cmd not in result["run_commands"]:
                    result["run_commands"].append(full_cmd)

            # 读取 dependencies
            deps = list(pkg.get("dependencies", {}).keys())
            dev_deps = list(pkg.get("devDependencies", {}).keys())
            result["dependencies"] = deps + [f"{d} (dev)" for d in dev_deps]
        except Exception:
            pass

    # 从 pyproject.toml 读取依赖
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            in_deps = False
            for line in text.split("\n"):
                line_stripped = line.strip()
                # 检测 section 切换，跳出 [project]
                if line_stripped.startswith("[") and not line_stripped.startswith("[project"):
                    in_deps = False
                    continue
                # 匹配 dependencies 行
                if line_stripped.startswith("dependencies"):
                    in_deps = True
                    # 提取 = 后面的内容
                    if "=" in line_stripped:
                        deps_part = line_stripped.split("=", 1)[1].strip()
                        # 去掉首尾的 [ ]
                        if deps_part.startswith("["):
                            deps_part = deps_part[1:]
                        if deps_part.endswith("]"):
                            deps_part = deps_part[:-1]
                            in_deps = False
                        for dep in deps_part.split(","):
                            dep = dep.strip().strip('"').strip("'").strip()
                            if dep and dep not in ("", "]", "["):
                                result["dependencies"].append(dep)
                    continue
                if in_deps:
                    cleaned = line_stripped.strip('",').strip()
                    if cleaned and not cleaned.startswith("#") and cleaned not in ("]", "["):
                        result["dependencies"].append(cleaned)
                    if "]" in line_stripped:
                        in_deps = False
        except Exception:
            pass

    # 检测 Makefile 中的命令
    makefile = root / "Makefile"
    if makefile.exists():
        try:
            text = makefile.read_text(encoding="utf-8")
            targets = re.findall(r'^([a-zA-Z][a-zA-Z0-9_-]*)\s*:', text, re.MULTILINE)
            for target in targets:
                full_cmd = f"make {target}"
                if any(kw in target.lower() for kw in _TEST_KEYWORDS):
                    if full_cmd not in result["test_commands"]:
                        result["test_commands"].append(full_cmd)
                elif any(kw in target.lower() for kw in _BUILD_KEYWORDS) and full_cmd not in result["build_commands"]:
                    result["build_commands"].append(full_cmd)
                elif any(kw in target.lower() for kw in _RUN_KEYWORDS) and full_cmd not in result["run_commands"]:
                    result["run_commands"].append(full_cmd)
        except Exception:
            pass

    # 检测 .github/workflows 中的 CI 命令
    workflows_dir = root / ".github" / "workflows"
    if workflows_dir.exists():
        for wf in workflows_dir.glob("*.yml"):
            try:
                text = wf.read_text(encoding="utf-8")
                # 提取 run: 后面的命令
                runs = re.findall(r'run:\s*(.+)$', text, re.MULTILINE)
                for run_cmd in runs:
                    run_cmd = run_cmd.strip()
                    if any(kw in run_cmd.lower() for kw in _TEST_KEYWORDS) and run_cmd not in result["test_commands"]:
                        result["test_commands"].append(run_cmd)
            except Exception:
                pass

    # 检测 Docker
    if (root / "Dockerfile").exists():
        result["frameworks"].append("Docker")

    return result


def _build_tree(root: Path, depth: int = 2, current_depth: int = 0,
                prefix: str = "", is_last: bool = True) -> list[str]:
    """递归构建文件树。"""
    if not root.exists() or not root.is_dir():
        return [f"{prefix}{'└── ' if is_last else '├── '}[目录不存在]"]
    if current_depth > depth:
        return [f"{prefix}{'└── ' if is_last else '├── '}..."]

    lines = []
    # 获取排序后的条目（目录优先）
    try:
        entries = sorted(
            root.iterdir(),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except PermissionError:
        return [f"{prefix}{'└── ' if is_last else '├── '}[权限不足]"]

    # 过滤排除项
    entries = [
        e for e in entries
        if (e.name not in _EXCLUDE_DIRS
        and not e.name.startswith("."))
        or e.name == ".env.example"  # 保留一些重要的点文件
        or e.name == ".gitignore"
    ]

    # 限制每个目录显示的条目数
    MAX_ENTRIES = 30
    if len(entries) > MAX_ENTRIES:
        truncated = entries[:MAX_ENTRIES]
        remaining = len(entries) - MAX_ENTRIES
    else:
        truncated = entries
        remaining = 0

    for i, entry in enumerate(truncated):
        is_last_entry = (i == len(truncated) - 1) and remaining == 0
        connector = "└── " if is_last_entry else "├── "
        line_prefix = prefix + connector

        if entry.is_dir():
            lines.append(f"{line_prefix}{entry.name}/")
            extension = "    " if is_last_entry else "│   "
            new_prefix = prefix + extension
            subtree = _build_tree(
                entry, depth, current_depth + 1,
                new_prefix, is_last_entry,
            )
            lines.extend(subtree)
        else:
            # 显示文件大小
            try:
                size = entry.stat().st_size
                if size >= 1024 * 1024:
                    size_str = f" ({size // (1024*1024)} MB)"
                elif size >= 1024:
                    size_str = f" ({size // 1024} KB)"
                else:
                    size_str = f" ({size} B)"
            except OSError:
                size_str = ""
            lines.append(f"{line_prefix}{entry.name}{size_str}")

    if remaining > 0:
        lines.append(f"{prefix}{'└── ' if is_last_entry else '├── '}... 还有 {remaining} 项")

    return lines


def scan_project(path: str | None = None, depth: int = 2,
                 include_patterns: list[str] | None = None) -> str:
    """扫描项目，返回结构化概览。"""
    root = Path(path).resolve() if path else Path.cwd()

    if not root.exists():
        return f"目录不存在: {root}"
    if not root.is_dir():
        return f"路径不是目录: {root}"

    # 限制深度
    depth = max(1, min(depth, 4))

    lines = []
    lines.append(f"📁 项目: {root.name}")
    lines.append(f"📂 路径: {root}")
    lines.append("")

    # ── 1. 语言和框架检测 ──
    info = _detect_language_and_framework(root)
    lines.append("🔤 技术栈：")
    if info["languages"]:
        lines.append(f"  语言: {', '.join(info['languages'])}")
    if info["frameworks"]:
        lines.append(f"  框架/工具: {', '.join(info['frameworks'])}")
    if info["config_files"]:
        lines.append(f"  配置文件: {', '.join(info['config_files'][:8])}")
    lines.append("")

    # ── 2. 构建/测试/运行命令 ──
    if info["build_commands"] or info["test_commands"] or info["run_commands"]:
        lines.append("⚡ 可用命令：")
        if info["build_commands"]:
            for cmd in info["build_commands"][:5]:
                lines.append(f"  🔨 {cmd}")
        if info["test_commands"]:
            for cmd in info["test_commands"][:5]:
                lines.append(f"  🧪 {cmd}")
        if info["run_commands"]:
            for cmd in info["run_commands"][:5]:
                lines.append(f"  ▶️  {cmd}")
        lines.append("")

    # ── 3. 依赖概览 ──
    if info["dependencies"]:
        dep_count = len(info["dependencies"])
        lines.append(f"📦 依赖（{dep_count} 个）：")
        # 只显示主要依赖的前 15 个
        shown = info["dependencies"][:15]
        for dep in shown:
            lines.append(f"  · {dep}")
        if dep_count > 15:
            lines.append(f"  ... 还有 {dep_count - 15} 个")
        lines.append("")

    # ── 4. 文件树 ──
    lines.append(f"🌳 文件树（深度 {depth}）：")
    tree = _build_tree(root, depth)
    lines.extend(tree)
    lines.append("")

    # ── 5. 统计 ──
    total_files = 0
    total_dirs = 0
    source_files = 0
    for f in root.rglob("*"):
        if any(part in _EXCLUDE_DIRS or part.startswith(".") for part in f.parts):
            continue
        if f.is_dir():
            total_dirs += 1
        else:
            total_files += 1
            if f.suffix in _SOURCE_EXTENSIONS:
                source_files += 1

    lines.append("📊 统计：")
    lines.append(f"  目录: {total_dirs}")
    lines.append(f"  文件: {total_files}")
    lines.append(f"  源码文件: {source_files}")
    if info["languages"]:
        lines.append(f"  主要语言: {info['languages'][0] if info['languages'] else '未知'}")

    return "\n".join(lines)


# ── 项目上下文注入 ──



# ── 项目上下文缓存 ──（前缀缓存保字节一致，避免每次重建前缀时重复扫描）
_PROJECT_CONTEXT_CACHE_TTL = 300  # 5 分钟，和 list_files 缓存一致
_project_context_cache: tuple[float, str, str] | None = None  # (ts, path, result)


def get_project_context(path: str | None = None) -> str | None:
    """生成项目上下文摘要，用于注入 system prompt。
    返回简洁的文本描述，如果项目无有效信息则返回 None。
    结果 5 分钟缓存（前缀缓存保字节一致，避免重建前缀时重复扫描）。
    """
    root = Path(path).resolve() if path else Path.cwd()

    if not root.exists() or not root.is_dir():
        return None

    # ── 缓存命中 ──
    global _project_context_cache
    now = time.time()
    root_str = str(root)
    if _project_context_cache is not None:
        ts, cached_path, result = _project_context_cache
        if (now - ts) < _PROJECT_CONTEXT_CACHE_TTL and cached_path == root_str:
            return result

    info = _detect_language_and_framework(root)

    parts = []
    if info["languages"]:
        parts.append(f"语言: {'/'.join(info['languages'][:3])}")
    if info["frameworks"]:
        parts.append(f"框架: {'/'.join(info['frameworks'][:3])}")

    commands = []
    if info["test_commands"]:
        commands.append(f"测试: {info['test_commands'][0]}")
    if info["build_commands"]:
        commands.append(f"构建: {info['build_commands'][0]}")
    if info["run_commands"]:
        commands.append(f"运行: {info['run_commands'][0]}")

    if not parts and not commands:
        # 至少检测一下 Git
        if (root / ".git").exists():
            result = f"当前项目: {root.name}（Git 仓库）"
            _project_context_cache = (now, root_str, result)
            return result
        return None

    context = f"当前项目: {root.name}"
    if parts:
        context += f" | {'，'.join(parts)}"
    if commands:
        context += f" | {'，'.join(commands)}"
    _project_context_cache = (now, root_str, context)
    return context


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "scan_project":
        return scan_project(
            path=args.get("path"),
            depth=args.get("depth", 2),
            include_patterns=args.get("include_patterns"),
        )
    return None
