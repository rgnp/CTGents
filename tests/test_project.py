"""测试 project 模块：scan_project / _detect_language_and_framework。"""

from pathlib import Path

from src.tools.project import (
    _build_tree,
    _detect_language_and_framework,
    get_project_context,
    scan_project,
)


class TestDetectLanguage:
    def test_detect_python(self, tmp_project):
        """pyproject.toml → Python + setuptools + pytest。"""
        info = _detect_language_and_framework(tmp_project)
        assert "Python" in info["languages"]
        assert any("setup" in f.lower() for f in info["frameworks"])
        assert "pytest" in info["test_commands"]

    def test_empty_project_fallback(self, tmp_empty_project):
        """无配置文件的纯 .py 项目 → 通过文件扩展名检测。"""
        info = _detect_language_and_framework(tmp_empty_project)
        assert "Python" in info["languages"]

    def test_docker_not_detected_when_not_present(self, tmp_project):
        """没有 Dockerfile → Docker 不在框架列表。"""
        info = _detect_language_and_framework(tmp_project)
        assert "Docker" not in info["frameworks"]

    def test_docker_detected_when_present(self, tmp_project):
        """有 Dockerfile → Docker 在框架列表。"""
        (tmp_project / "Dockerfile").write_text("FROM python:3.11\n")
        info = _detect_language_and_framework(tmp_project)
        assert "Docker" in info["frameworks"]

    def test_detect_wildcard_csproj(self, tmp_path):
        """*.csproj 通配符 → 检测到 C#。"""
        (tmp_path / "test.csproj").write_text("<Project />")
        info = _detect_language_and_framework(tmp_path)
        assert "C#" in info["languages"]
        assert "dotnet test" in info["test_commands"]

    def test_detect_wildcard_sln(self, tmp_path):
        """*.sln 通配符 → 检测到 .NET Solution。"""
        (tmp_path / "test.sln").write_text("\n")
        info = _detect_language_and_framework(tmp_path)
        assert "C#" in info["languages"]

    def test_detect_package_json(self, tmp_path):
        """package.json 含 scripts → 检测 JS/TS。"""
        import json
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {
                "test": "jest",
                "build": "tsc",
                "dev": "nodemon",
            },
            "dependencies": {"express": "^4.0"},
            "devDependencies": {"jest": "^29.0"},
        }))
        info = _detect_language_and_framework(tmp_path)
        assert "JavaScript/TypeScript" in info["languages"]
        assert "npm run test" in info["test_commands"]
        assert "npm run build" in info["build_commands"]
        assert "npm run dev" in info["run_commands"]
        # 依赖也应该被捕获
        assert any("express" in d for d in info["dependencies"])
        assert any("jest (dev)" in d for d in info["dependencies"])

    def test_detect_pyproject_deps(self, tmp_path):
        """pyproject.toml 含 dependencies 列表。"""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\n'
            'name = "test"\n'
            'dependencies = ["requests", "click", "rich"]\n'
        )
        info = _detect_language_and_framework(tmp_path)
        assert "Python" in info["languages"]
        assert "requests" in info["dependencies"]
        assert "click" in info["dependencies"]

    def test_detect_pyproject_multiline_deps(self, tmp_path):
        """pyproject.toml 多行 dependencies。"""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\n'
            'name = "test"\n'
            'dependencies = [\n'
            '  "requests",\n'
            '  "click",\n'
            '  "rich",\n'
            ']\n'
        )
        info = _detect_language_and_framework(tmp_path)
        assert "Python" in info["languages"]
        assert "requests" in info["dependencies"]

    def test_detect_makefile_targets(self, tmp_path):
        """Makefile 含 build/test/run 目标。"""
        (tmp_path / "Makefile").write_text(
            ".PHONY: test build lint\n\n"
            "test:\n\tpytest\n\n"
            "build:\n\tpython setup.py\n\n"
            "lint:\n\truff\n"
        )
        info = _detect_language_and_framework(tmp_path)
        assert "make test" in info["test_commands"]
        assert "make build" in info["build_commands"]
        assert "make lint" in info["test_commands"]

    def test_detect_github_workflows(self, tmp_path):
        """CI 工作流检测命令。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n"
            "  test:\n"
            "    steps:\n"
            "      - run: pytest --cov\n"
            "      - run: ruff check src/\n"
        )
        info = _detect_language_and_framework(tmp_path)
        assert "pytest --cov" in info["test_commands"]

    def test_detect_go_mod(self, tmp_path):
        """go.mod → Go。"""
        (tmp_path / "go.mod").write_text("module test\n")
        info = _detect_language_and_framework(tmp_path)
        assert "Go" in info["languages"]

    def test_detect_cargo_toml(self, tmp_path):
        """Cargo.toml → Rust。"""
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        info = _detect_language_and_framework(tmp_path)
        assert "Rust" in info["languages"]


class TestBuildTree:
    def test_builds_tree(self, tmp_project):
        """能生成文件树，且包含关键文件。"""
        tree = _build_tree(tmp_project, depth=2)
        tree_str = "\n".join(tree)
        assert "src" in tree_str
        assert "pyproject.toml" in tree_str
        assert "README.md" in tree_str

    def test_depth_limit(self, tmp_project):
        """depth=1 时不展开子目录。"""
        tree = _build_tree(tmp_project, depth=1)
        "\n".join(tree)
        assert len(tree) > 0

    def test_filter_excludes(self, tmp_project):
        """排除 .git、__pycache__ 等目录。"""
        (tmp_project / "__pycache__").mkdir()
        tree = _build_tree(tmp_project, depth=2)
        tree_str = "\n".join(tree)
        assert "__pycache__" not in tree_str

    def test_nonexistent_directory(self):
        tree = _build_tree(Path("/nonexistent_path_xyz"), depth=2)
        tree_str = "\n".join(tree)
        assert isinstance(tree_str, str)

    def test_truncation_many_entries(self, tmp_path):
        """30+ 条目 → 截断并显示还有 N 项。"""
        for i in range(35):
            (tmp_path / f"file_{i:02d}.txt").write_text("x")
        tree = _build_tree(tmp_path, depth=1)
        tree_str = "\n".join(tree)
        assert "还有" in tree_str


class TestScanProject:
    def test_scan_valid_project(self, tmp_project):
        result = scan_project(path=str(tmp_project))
        assert "项目" in result
        assert tmp_project.name in result
        assert "Python" in result
        assert "技术栈" in result or "语言" in result

    def test_scan_empty_project(self, tmp_empty_project):
        result = scan_project(path=str(tmp_empty_project))
        assert "项目" in result
        assert tmp_empty_project.name in result

    def test_invalid_path(self):
        result = scan_project(path="/nonexistent/path/12345")
        assert "不存在" in result

    def test_depth_parameter(self, tmp_project):
        """depth=1 精简输出。"""
        result_1 = scan_project(path=str(tmp_project), depth=1)
        result_3 = scan_project(path=str(tmp_project), depth=3)
        assert isinstance(result_1, str)
        assert isinstance(result_3, str)

    def test_scan_file_not_directory(self, tmp_project):
        """传入文件路径而不是目录 → 返回错误。"""
        f = tmp_project / "README.md"
        result = scan_project(path=str(f))
        assert "不是目录" in result


class TestGetProjectContext:
    def test_returns_context(self, tmp_project):
        context = get_project_context(path=str(tmp_project))
        assert context is not None
        assert tmp_project.name in context

    def test_none_for_invalid(self):
        context = get_project_context(path="/nonexistent/path/12345")
        assert context is None

    def test_cache_hit(self, tmp_project):
        """缓存命中：第二次调用不重复扫描。"""
        ctx1 = get_project_context(path=str(tmp_project))
        ctx2 = get_project_context(path=str(tmp_project))
        assert ctx1 == ctx2

    def test_git_only_project(self, tmp_path):
        """只有 .git 无其他配置文件 → 返回 Git 仓库上下文。"""
        (tmp_path / ".git").mkdir()
        ctx = get_project_context(path=str(tmp_path))
        assert ctx is not None
        assert "Git 仓库" in ctx
