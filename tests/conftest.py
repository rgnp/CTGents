"""pytest 共享 fixtures。"""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path_factory, monkeypatch):
    """把所有可变运行数据隔离到临时 workspace。

    测试不得读取或污染真实 current.md、回执、Gap、统计和会话摘要。需要特定路径
    的测试可在 test body 中再次 monkeypatch（发生得更晚，覆盖本 fixture）。
    """
    import src.asset_usage as _asset_usage
    import src.gaps as _gaps
    import src.llm as _llm
    import src.main as _main
    import src.paths as _paths
    import src.session_summary as _session_summary
    import src.tasks as _tasks
    import src.tracker as _tracker
    import src.verification_receipts as _verification
    import src.work_receipts as _work_receipts
    from src.tools import rag as _rag

    runtime = tmp_path_factory.mktemp("ctg-runtime")
    task_dir = runtime / "tasks"
    stats_dir = runtime / "stats"
    knowledge_dir = runtime / "knowledge"
    task_dir.mkdir(parents=True)

    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", runtime)
    monkeypatch.setattr(_paths, "KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(_paths, "MEMORY_DIR", runtime / "memory")
    monkeypatch.setattr(_paths, "SESSIONS_DIR", runtime / "sessions")
    monkeypatch.setattr(_paths, "STATS_DIR", stats_dir)
    monkeypatch.setattr(_paths, "TASKS_DIR", task_dir)
    monkeypatch.setattr(_paths, "RAG_INDEX_DIR", runtime / ".rag-index")
    monkeypatch.setattr(_tasks, "TASKS_DIR", task_dir)
    monkeypatch.setattr(_tasks, "CURRENT_TASK_FILE", task_dir / "current.md")
    monkeypatch.setattr(_tasks, "AMBITIONS_FILE", task_dir / "ambitions.md")
    monkeypatch.setattr(_tasks, "ARCHIVE_DIR", task_dir / "archive")
    monkeypatch.setattr(_asset_usage, "USAGE_FILE", task_dir / "asset-usage.jsonl")
    monkeypatch.setattr(_work_receipts, "WORK_RECEIPTS_FILE", task_dir / "work-receipts.jsonl")
    monkeypatch.setattr(_verification, "RECEIPTS_FILE", task_dir / "verification-receipts.jsonl")
    monkeypatch.setattr(_gaps, "_GAP_LEDGER_FILE", task_dir / "gap-ledger.json")
    monkeypatch.setattr(_gaps, "_GAP_CACHE_FILE", runtime / ".gap_cache.json")
    monkeypatch.setattr(_tracker, "_STATS_DIR", stats_dir)
    monkeypatch.setattr(_llm, "_STATS_DIR", stats_dir)
    monkeypatch.setattr(_llm, "_PAYLOAD_DIR", stats_dir / "payloads")
    monkeypatch.setattr(_main, "_STATE_FILE", runtime / "sessions" / "_state.md")
    monkeypatch.setattr(_main, "_ERRORS_FILE", runtime / "sessions" / "_errors.md")
    monkeypatch.setattr(
        _session_summary,
        "_KNOWLEDGE_SESSIONS_DIR",
        knowledge_dir / "sessions",
    )
    monkeypatch.setattr(_rag, "_KNOWLEDGE_DIR", knowledge_dir)
    monkeypatch.setattr(_rag, "RAG_INDEX_DIR", str(runtime / ".rag-index"))


@pytest.fixture(autouse=True)
def _no_llm_session_summary(monkeypatch):
    """掐断会话摘要的真 LLM 调用（extract_summary 默认 use_llm 读 CTG_SUMMARY_LLM=开），
    否则任何触发会话收尾/摘要的测试都会打真 API。测试默认全走规则层兜底。
    要测 LLM 层的测试：在测试模块顶部 `from src.session_summary import _llm_summarize`
    拿原函数（import 发生在收集期、早于本 fixture 打补丁），再自行 mock backend。
    """
    import src.session_summary as _ss
    monkeypatch.setattr(_ss, "_llm_summarize", lambda _m: None)


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


class _DummyCtx:
    """最小化 CacheContext mock，供 verifier 等不涉 LLM 的测试用。"""

    def __init__(self) -> None:
        self.log: list[dict] = []
        self.control_signal: str | None = None
        self.control_payload: str | None = None


@pytest.fixture
def dummy_ctx() -> _DummyCtx:
    """返回仅含 log + control_signal 的最小 ctx。"""
    return _DummyCtx()
