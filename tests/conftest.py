"""pytest 共享 fixtures。"""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_task_state(tmp_path, monkeypatch):
    """把长任务状态(current.md/ambitions)隔离到临时空文件，并重置会话级一次性缓存。

    否则回合驱动测试会读到开发/agent 实时的 tasks/current.md：一旦那里留了未完成
    步骤([ ]/[o])，has_unfinished() 即为真 → 自动续做多跑一轮 → 脚本化 mock 迭代器
    StopIteration。即"套件成败取决于实时任务状态"的设计性 flaky。需要任务的测试自行
    monkeypatch CURRENT_TASK_FILE 覆盖（覆盖在 test body 中发生，晚于本 autouse，胜出）。
    """
    import src.tasks as _tasks
    monkeypatch.setattr(_tasks, "CURRENT_TASK_FILE", tmp_path / "_no_current.md")
    monkeypatch.setattr(_tasks, "AMBITIONS_FILE", tmp_path / "_no_ambitions.md")
    # _gaps_reported=True：抑制测试期触发真实 detect_all_gaps(慢+读真实代码库+不确定)。
    # _task_suggested=False：让建任务建议可触发。二者均 monkeypatch，teardown 自动还原。
    monkeypatch.setattr(_tasks, "_gaps_reported", True)
    monkeypatch.setattr(_tasks, "_task_suggested", False)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """创建一个模拟项目目录，包含基本文件结构。"""
    # 核心目录
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tools").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "tools" / "__init__.py").write_text("")

    # 核心配置文件（含 [project.scripts] 触发 pyproject 脚本检测）
    (tmp_path / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=64"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[project]\n"
        'name = "test-project"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[project.scripts]\n"
        'ctg = "src.main:main"\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 120\n"
    )

    # README（含命令示例）
    (tmp_path / "README.md").write_text(
        "# Test Project\n\n"
        "## Quick Start\n"
        "pip install -r requirements.txt\n"
        "python run.py\n"
    )

    # AGENTS.md（含三级边界 + 安全章节 + 命令章节）
    (tmp_path / "AGENTS.md").write_text(
        "# AGENTS.md\n\n"
        "## 命令\n"
        "pytest\n\n"
        "## 边界\n"
        "### 始终执行\n"
        "- 读取文件\n\n"
        "### 事先询问\n"
        "- 修改配置\n\n"
        "### 绝不执行\n"
        "- 提交密钥\n\n"
        "## 安全\n"
        "- API 密钥通过 .env 管理\n"
    )

    # .gitignore
    (tmp_path / ".gitignore").write_text(".env\n__pycache__\n.venv\n")

    # .editorconfig
    (tmp_path / ".editorconfig").write_text("root = true\n[*]\nindent_style = space\n")

    # Makefile（含 test 目标）
    (tmp_path / "Makefile").write_text(
        ".PHONY: test\n\ntest:\n\tpytest -v\n"
    )

    # docs/roadmap.md（加分项）
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text(
        "# 路线图\n\n## v0.1\n基础功能\n"
    )

    # 源码文件
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n")
    (tmp_path / "src" / "config.py").write_text('SECRET = "test"\n')

    # .git（标记为 Git 仓库）
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/master\n")

    return tmp_path


@pytest.fixture
def tmp_empty_project(tmp_path: Path) -> Path:
    """一个空空如也的"项目"，用于测试最低分场景。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "random_code.py").write_text("x = 1\n")
    return tmp_path


@pytest.fixture
def clean_cwd():
    """保存并恢复当前工作目录。"""
    old = os.getcwd()
    yield
    os.chdir(old)
