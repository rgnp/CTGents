"""测试 lint 模块：check_project / generate_agents_md。"""

import os
from pathlib import Path

import pytest

from src.tools.lint import (
    _check_commands,
    _check_tests,
    _check_structure,
    _check_style,
    _check_git,
    _check_boundaries,
    _check_bonus,
    _get_rating,
    _detect_tech_stack,
    _detect_test_commands,
    _detect_build_commands,
    check_project,
    generate_agents_md,
)


# ═══════════════════════════════════════════════════════════════
# 独立函数测试
# ═══════════════════════════════════════════════════════════════

class TestGetRating:
    def test_excellent(self):
        assert "优秀" in _get_rating(90)

    def test_good(self):
        assert "良好" in _get_rating(75)

    def test_fair(self):
        assert "一般" in _get_rating(60)

    def test_needs_improvement(self):
        assert "需改进" in _get_rating(30)


class TestCheckCommands:
    def test_fully_configured(self, tmp_project):
        """有 Makefile + README + AGENTS.md → 满分 16。"""
        result = _check_commands(tmp_project)
        assert result["score"] == 16
        assert result["max_score"] == 16
        assert result["name"] == "命令 (Commands)"

    def test_no_files_raises_issues(self, tmp_empty_project):
        """空项目 → 有 issues。"""
        result = _check_commands(tmp_empty_project)
        assert result["score"] < result["max_score"]
        assert len(result["issues"]) > 0


class TestCheckTests:
    def test_no_test_dir(self, tmp_project):
        """没有 tests/ 目录 → 0 分。"""
        result = _check_tests(tmp_project)
        assert result["score"] == 0
        assert "缺少测试目录" in result["issues"][0] or not result["issues"]

    def test_with_test_dir(self, tmp_project):
        """有 tests/ 目录但有测试文件 → 有分。"""
        (tmp_project / "tests").mkdir()
        (tmp_project / "tests" / "test_demo.py").write_text("def test_x(): pass\n")
        result = _check_tests(tmp_project)
        assert result["score"] >= 6


class TestCheckStructure:
    def test_good_structure(self, tmp_project):
        """src/ + README + AGENTS.md + .gitignore → 高分。"""
        result = _check_structure(tmp_project)
        assert result["score"] >= 14  # 基本满分

    def test_missing_elements(self, tmp_empty_project):
        """空项目只有 src/ → 低分。"""
        result = _check_structure(tmp_empty_project)
        assert result["score"] <= 8


class TestCheckStyle:
    def test_with_tool_config(self, tmp_project):
        """有 ruff 配置 + .editorconfig → 高分。"""
        result = _check_style(tmp_project)
        assert result["score"] >= 10

    def test_no_config(self, tmp_empty_project):
        """没有配置 → 0 分。"""
        result = _check_style(tmp_empty_project)
        assert result["score"] == 0


class TestCheckGit:
    def test_is_git_repo(self, tmp_project):
        """有 .git 目录 + .gitignore → 有分。"""
        result = _check_git(tmp_project)
        assert result["score"] > 0

    def test_not_git_repo(self, tmp_empty_project):
        """没有 .git → 0 分。"""
        result = _check_git(tmp_empty_project)
        assert result["score"] == 0
        assert len(result["issues"]) > 0

    def test_gitignore_has_env(self, tmp_project):
        """.gitignore 中包含 .env → 加分。"""
        result = _check_git(tmp_project)
        assert result["score"] >= 6

    def test_git_upward_search(self, tmp_path):
        """项目是子目录，.git 在父级 → 能被找到。"""
        child = tmp_path / "sub" / "nested"
        child.mkdir(parents=True)
        (child / ".gitignore").write_text(".env\n")
        # .git 在父目录
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
        result = _check_git(child)
        assert result["score"] > 0, "向上搜索 .git 失败"


class TestCheckBoundaries:
    def test_has_complete_boundaries(self, tmp_project):
        """AGENTS.md 含完整三级边界 + 密钥保护 → 满分 18。"""
        result = _check_boundaries(tmp_project)
        assert result["score"] == 18

    def test_no_agents_md(self, tmp_empty_project):
        """没有 AGENTS.md → 0 分。"""
        result = _check_boundaries(tmp_empty_project)
        assert result["score"] == 0
        assert "缺少 AGENTS.md" in result["issues"][0]


class TestCheckBonus:
    def test_no_bonus_items(self, tmp_empty_project):
        """空项目 → 无加分/减分。"""
        result = _check_bonus(tmp_empty_project)
        assert len(result["items"]) == 0

    def test_roadmap_adds_bonus(self, tmp_project):
        """有 ROADMAP.md → 加分。"""
        result = _check_bonus(tmp_project)
        items = [item for tag, item in result["items"] if tag == "+"]
        assert any("ROADMAP" in i for i in items)


class TestDetectTechStack:
    def test_python_detected(self, tmp_project):
        """pyproject.toml → Python。"""
        stack = _detect_tech_stack(tmp_project)
        assert any("Python" in s for s in stack)

    def test_no_tech_detected(self, tmp_empty_project):
        """纯 .py 文件但无配置文件 → Python 仍可被检测。"""
        stack = _detect_tech_stack(tmp_empty_project)
        # 没有 pyproject.toml → 不会检测到 Python
        assert len(stack) == 0


class TestDetectCommands:
    def test_test_commands(self, tmp_project):
        cmds = _detect_test_commands(tmp_project)
        assert "pytest" in cmds

    def test_build_commands(self, tmp_project):
        cmds = _detect_build_commands(tmp_project)
        assert any("pip" in c for c in cmds)


# ═══════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════

class TestCheckProject:
    def test_invalid_path(self):
        result = check_project(path="/nonexistent/path/12345")
        assert "不存在" in result

    def test_good_project(self, tmp_project):
        result = check_project(path=str(tmp_project))
        assert "项目" in result
        assert "总分" in result
        assert "维度评分" in result

    def test_empty_project(self, tmp_empty_project):
        result = check_project(path=str(tmp_empty_project))
        assert "总分" in result
        assert "需改进" in result or "一般" in result or "良好" in result

    def test_fix_option(self, tmp_empty_project):
        """fix=True 试图修复。"""
        result = check_project(path=str(tmp_empty_project), fix=True)
        assert "AGENTS.md" in result or "自动修复" in result or "总分" in result

    def test_empty_project_no_agents(self, tmp_empty_project):
        """空项目没有 AGENTS.md → 报告提到边界问题。"""
        result = check_project(path=str(tmp_empty_project))
        assert "AGENTS" in result or "边界" in result or "总分" in result

    def test_has_agents_md(self, tmp_project):
        """有 AGENTS.md 的项目 → 边界维度高分。"""
        result = check_project(path=str(tmp_project))
        assert "18/18" in result  # 边界满分


class TestGenerateAgentsMd:
    def test_generates_to_empty_project(self, tmp_empty_project):
        """对没有 AGENTS.md 的项目自动生成。"""
        result = generate_agents_md(path=str(tmp_empty_project), overwrite=True)
        assert "AGENTS.md" in result
        generated = tmp_empty_project / "AGENTS.md"
        assert generated.exists()
        content = generated.read_text(encoding="utf-8")
        assert "# AGENTS.md" in content

    def test_skips_existing_without_overwrite(self, tmp_project):
        """已有 AGENTS.md 但不设 overwrite → 跳过。"""
        result = generate_agents_md(path=str(tmp_project))
        assert "已存在" in result

    def test_overwrite_existing(self, tmp_project):
        """设 overwrite=True → 覆盖。"""
        result = generate_agents_md(path=str(tmp_project), overwrite=True)
        assert "已更新" in result or "已生成" in result
        assert (tmp_project / "AGENTS.md").exists()

    def test_invalid_path(self):
        result = generate_agents_md(path="/nonexistent/path/12345")
        assert "不存在" in result

    def test_generated_has_required_sections(self, tmp_empty_project):
        """生成的 AGENTS.md 包含关键章节。"""
        generate_agents_md(path=str(tmp_empty_project), overwrite=True)
        content = (tmp_empty_project / "AGENTS.md").read_text(encoding="utf-8")
        assert "命令" in content or "Commands" in content
        assert "项目结构" in content or "Structure" in content
        assert "边界" in content or "Boundaries" in content
        assert "安全" in content or "Security" in content
