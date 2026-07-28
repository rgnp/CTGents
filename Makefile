# Agent 项目 Makefile
# 跨平台：Windows 用 py 启动器，Linux/macOS 用 python3；可用 PYTHON=xxx 覆盖。

ifeq ($(OS),Windows_NT)
PYTHON := py
RMPYCACHE := @for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
RMCACHEDIRS := @for /d %%d in (.pytest_cache .mypy_cache .ruff_cache) do @if exist "%%d" rd /s /q "%%d" 2>nul
else
PYTHON := python3
RMPYCACHE := @find . -type d -name __pycache__ -exec rm -rf {} +
RMCACHEDIRS := @rm -rf .pytest_cache .mypy_cache .ruff_cache
endif
PYTEST := $(PYTHON) -m pytest

.PHONY: help install test test-fast lint lint-fix run clean check precommit precommit-install coverage docs-sync preflight

help:
	@echo "可用目标："
	@echo "  make install        安装依赖"
	@echo "  make test           运行全部测试（含标 slow 的慢测试）"
	@echo "  make test-fast      只跑快测试（-m \"not slow\"），提交前用这个"
	@echo "  make lint           代码检查（ruff check）"
	@echo "  make lint-fix       自动修复 lint 问题"
	@echo "  make run            启动 Agent"
	@echo "  make clean          清理临时文件"
	@echo "  make check          项目规范扫描"
	@echo "  make precommit      手动运行 pre-commit 检查"
	@echo "  make precommit-install  安装 pre-commit hooks"
	@echo "  make coverage       测试覆盖率报告"
	@echo "  make docs-sync      检查文档是否同步"
	@echo "  make preflight      一站式 lint + test + docs-sync + check"

install:
	pip install -e ".[dev]"

test:
	$(PYTEST) -v

test-fast:
	$(PYTEST) -v -m "not slow"

lint:
	ruff check src/

lint-fix:
	ruff check --fix src/
	ruff format src/

run:
	$(PYTHON) run.py

clean:
	@echo "清理 __pycache__ ..."
	$(RMPYCACHE)
	@echo "清理 .pytest_cache .mypy_cache .ruff_cache ..."
	$(RMCACHEDIRS)
	@echo "清理完成"

check:
	$(PYTHON) -c "from src.tools.lint import check_project; print(check_project())"

precommit-install:
	pre-commit install

precommit:
	pre-commit run --all-files

coverage:
	$(PYTEST) --cov --cov-report=term --cov-report=html

docs-sync:
	$(PYTHON) -c "from src.tools.lint import docs_sync_check; print(docs_sync_check())"

preflight: lint test docs-sync check
	@echo "✅ 全部检查通过"
