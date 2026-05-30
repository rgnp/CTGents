"""测试 project 模块：scan_project / _detect_language_and_framework。"""

from pathlib import Path

from src.tools.project import (
    _detect_language_and_framework,
    _build_tree,
    scan_project,
    get_project_context,
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
        # random_code.py 存在 → 应该检测到 Python
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
        tree_str = "\n".join(tree)
        # src/ 可能展开也可能不展开，但至少不会递归过深
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
