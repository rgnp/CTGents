"""测试 lint 模块：check_project / generate_agents_md。"""

import json

from src.tools.lint import (
    _build_file_tree,
    _check_bonus,
    _check_boundaries,
    _check_commands,
    _check_git,
    _check_structure,
    _check_style,
    _check_tests,
    _classify_changed_file,
    _detect_build_commands,
    _detect_tech_stack,
    _detect_test_commands,
    _generate_agents_md_content,
    _get_rating,
    check_project,
    docs_sync_check,
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
        result = _check_commands(tmp_project)
        assert result["score"] == 16
        assert result["max_score"] == 16

    def test_no_files_raises_issues(self, tmp_empty_project):
        result = _check_commands(tmp_empty_project)
        assert result["score"] < result["max_score"]
        assert len(result["issues"]) > 0

    def test_package_json_scripts(self, tmp_path):
        """package.json 有 scripts → 检测到。"""
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "build": "tsc"}}),
            encoding="utf-8",
        )
        result = _check_commands(tmp_path)
        assert result["score"] >= 4

    def test_pyproject_scripts(self, tmp_path):
        """pyproject.toml 含 scripts → 检测到。"""
        (tmp_path / "pyproject.toml").write_text(
            "[project.scripts]\ntest = 'pytest'\n", encoding="utf-8"
        )
        result = _check_commands(tmp_path)
        assert result["score"] >= 3

    def test_readme_with_commands(self, tmp_path):
        """README 含命令行关键词。"""
        (tmp_path / "README.md").write_text("Run: pytest tests/", encoding="utf-8")
        result = _check_commands(tmp_path)
        assert result["score"] >= 4

    def test_agents_md_no_commands_section(self, tmp_path):
        """AGENTS.md 存在但无命令章节。"""
        (tmp_path / "AGENTS.md").write_text("# Hello\n", encoding="utf-8")
        result = _check_commands(tmp_path)
        issues = " ".join(result["issues"])
        assert "命令" in issues


class TestCheckTests:
    def test_no_test_dir(self, tmp_project):
        result = _check_tests(tmp_project)
        assert result["score"] == 0
        assert any("缺少" in i for i in result["issues"])

    def test_with_test_dir(self, tmp_project):
        (tmp_project / "tests").mkdir()
        (tmp_project / "tests" / "test_demo.py").write_text("def test_x(): pass\n")
        result = _check_tests(tmp_project)
        assert result["score"] >= 6

    def test_test_dir_no_files(self, tmp_path):
        """有 tests/ 目录但没有测试文件。"""
        (tmp_path / "tests").mkdir()
        result = _check_tests(tmp_path)
        assert result["score"] >= 6  # 有目录就有基础分
        assert any("未找到" in i for i in result.get("issues", []))

    def test_with_pytest_config(self, tmp_path):
        """Pytest 配置存在。"""
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("def test(): pass\n")
        result = _check_tests(tmp_path)
        assert result["score"] >= 9


class TestCheckStructure:
    def test_good_structure(self, tmp_project):
        result = _check_structure(tmp_project)
        assert result["score"] >= 14

    def test_missing_elements(self, tmp_empty_project):
        result = _check_structure(tmp_empty_project)
        assert result["score"] <= 8

    def test_oversized_init(self, tmp_path):
        """__init__.py 过大 → 触发警告。"""
        src = tmp_path / "src"
        src.mkdir()
        init = src / "__init__.py"
        init.write_text("# " + "x" * 11000, encoding="utf-8")
        (tmp_path / "README.md").write_text("")  # 拿基础分
        (tmp_path / ".gitignore").write_text("")
        result = _check_structure(tmp_path)
        assert any("过大" in i for i in result.get("issues", []))

    def test_flat_src_many_files(self, tmp_path):
        """src/ 下超过 15 个 .py 文件 → 扁平化警告。"""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(20):
            (src / f"module_{i}.py").write_text("pass\n")
        (tmp_path / "README.md").write_text("")
        (tmp_path / ".gitignore").write_text("")
        result = _check_structure(tmp_path)
        assert any("分组到子目录" in i for i in result.get("suggestions", []))


class TestCheckStyle:
    def test_with_tool_config(self, tmp_project):
        result = _check_style(tmp_project)
        assert result["score"] >= 10

    def test_no_config(self, tmp_empty_project):
        result = _check_style(tmp_empty_project)
        assert result["score"] == 0


class TestCheckGit:
    def test_is_git_repo(self, tmp_project):
        result = _check_git(tmp_project)
        assert result["score"] > 0

    def test_not_git_repo(self, tmp_empty_project):
        result = _check_git(tmp_empty_project)
        assert result["score"] == 0

    def test_git_upward_search(self, tmp_path):
        child = tmp_path / "sub" / "nested"
        child.mkdir(parents=True)
        (child / ".gitignore").write_text(".env\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
        result = _check_git(child)
        assert result["score"] > 0


class TestCheckBoundaries:
    def test_has_complete_boundaries(self, tmp_project):
        result = _check_boundaries(tmp_project)
        assert result["score"] == 18

    def test_no_agents_md(self, tmp_empty_project):
        result = _check_boundaries(tmp_empty_project)
        assert result["score"] == 0

    def test_partial_boundaries(self, tmp_path):
        """AGENTS.md 只有部分边界 → 扣分。"""
        (tmp_path / "AGENTS.md").write_text("## 安全\n密钥保护\n", encoding="utf-8")
        result = _check_boundaries(tmp_path)
        assert result["score"] < 18
        assert result["score"] > 0


class TestCheckBonus:
    def test_no_bonus_items(self, tmp_empty_project):
        result = _check_bonus(tmp_empty_project)
        assert len(result["items"]) == 0

    def test_roadmap_adds_bonus(self, tmp_project):
        result = _check_bonus(tmp_project)
        items = [item for tag, item in result["items"] if tag == "+"]
        assert any("roadmap" in i.lower() for i in items)

    def test_changelog_adds_bonus(self, tmp_path):
        """docs/changelog.md → 加分。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "changelog.md").write_text("v1.0\n")
        result = _check_bonus(tmp_path)
        items = [item for tag, item in result["items"] if tag == "+"]
        assert any("changelog" in i.lower() for i in items)

    def test_contributing_adds_bonus(self, tmp_path):
        """docs/contributing.md → 加分。"""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "contributing.md").write_text("# guide\n")
        result = _check_bonus(tmp_path)
        items = [item for tag, item in result["items"] if tag == "+"]
        assert any("contributing" in i.lower() for i in items)

    def test_github_actions_adds_bonus(self, tmp_path):
        """.github/workflows → 加分。"""
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: CI\n")
        result = _check_bonus(tmp_path)
        items = [item for tag, item in result["items"] if tag == "+"]
        assert any("GitHub Actions" in i for i in items)

    def test_node_modules_penalty(self, tmp_path):
        """node_modules/ 根目录 → 减分。"""
        (tmp_path / "node_modules").mkdir()
        result = _check_bonus(tmp_path)
        items = [item for tag, item in result["items"] if tag == "-"]
        assert any("node_modules" in i for i in items)


class TestDetectTechStack:
    def test_python_detected(self, tmp_project):
        stack = _detect_tech_stack(tmp_project)
        assert any("Python" in s for s in stack)

    def test_no_tech_detected(self, tmp_empty_project):
        stack = _detect_tech_stack(tmp_empty_project)
        assert len(stack) == 0

    def test_node_detected(self, tmp_path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        stack = _detect_tech_stack(tmp_path)
        assert any("Node" in s for s in stack)

    def test_rust_detected(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        stack = _detect_tech_stack(tmp_path)
        assert any("Rust" in s for s in stack)

    def test_go_detected(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        stack = _detect_tech_stack(tmp_path)
        assert any("Go" in s for s in stack)


class TestDetectCommands:
    def test_test_commands(self, tmp_project):
        cmds = _detect_test_commands(tmp_project)
        assert "pytest" in cmds

    def test_build_commands(self, tmp_project):
        cmds = _detect_build_commands(tmp_project)
        assert any("pip" in c for c in cmds)

    def test_node_test_commands(self, tmp_path):
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        cmds = _detect_test_commands(tmp_path)
        assert "npm test" in cmds

    def test_cargo_test(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cmds = _detect_test_commands(tmp_path)
        assert "cargo test" in cmds

    def test_go_test(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        cmds = _detect_test_commands(tmp_path)
        assert "go test" in cmds[0]


class TestBuildFileTree:
    def test_build_tree(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        (tmp_path / "README.md").write_text("# hello")
        tree = _build_file_tree(tmp_path)
        assert "src/" in tree
        assert "main.py" in tree


class TestGenerateAgentsMdContent:
    def test_generates_to_file(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='test'\nrequires-python='>=3.11'\n"
            "[project.scripts]\ntest='pytest'\n"
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("# Test")
        out = tmp_path / "OUT_AGENTS.md"
        _generate_agents_md_content(tmp_path, out)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "AGENTS.md" in content
        assert "pytest" in content


class TestClassifyChangedFile:
    def test_exact_match(self):
        assert _classify_changed_file("src/llm.py") == "src/llm.py"

    def test_tools_match(self):
        key = _classify_changed_file("src/tools/lint.py")
        assert key == "src/tools/lint.py"

    def test_no_match(self):
        assert _classify_changed_file("random/file.py") == ""


class TestDocsSyncCheck:
    def test_no_changes_in_temp(self, tmp_path, monkeypatch):
        """临时目录无 Git 变更 → 提示无变更。"""
        monkeypatch.chdir(tmp_path)
        result = docs_sync_check(path=str(tmp_path))
        assert "无法获取" in result or "没有检测到变更" in result

    def test_current_project(self):
        """对当前项目运行 — 至少返回合法结果。"""
        result = docs_sync_check()
        assert isinstance(result, str)
        assert len(result) > 0


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

    def test_empty_project(self, tmp_empty_project):
        result = check_project(path=str(tmp_empty_project))
        assert "总分" in result

    def test_fix_option(self, tmp_empty_project):
        result = check_project(path=str(tmp_empty_project), fix=True)
        assert "AGENTS.md" in result or "总分" in result

    def test_excellent_project(self, tmp_project):
        """高分项目 → 优秀评级。"""
        result = check_project(path=str(tmp_project))
        assert "优秀" in result or "良好" in result


class TestGenerateAgentsMd:
    def test_generates_to_empty_project(self, tmp_empty_project):
        result = generate_agents_md(path=str(tmp_empty_project), overwrite=True)
        assert "已生成" in result
        generated = tmp_empty_project / "AGENTS.md"
        assert generated.exists()

    def test_skips_existing_without_overwrite(self, tmp_project):
        result = generate_agents_md(path=str(tmp_project))
        assert "已存在" in result

    def test_overwrite_existing(self, tmp_project):
        result = generate_agents_md(path=str(tmp_project), overwrite=True)
        assert "已更新" in result

    def test_invalid_path(self):
        result = generate_agents_md(path="/nonexistent/path/12345")
        assert "不存在" in result

    def test_generated_has_required_sections(self, tmp_empty_project):
        generate_agents_md(path=str(tmp_empty_project), overwrite=True)
        content = (tmp_empty_project / "AGENTS.md").read_text(encoding="utf-8")
        assert "命令" in content or "Commands" in content
        assert "项目结构" in content or "Structure" in content
        assert "边界" in content or "Boundaries" in content
        assert "安全" in content or "Security" in content
