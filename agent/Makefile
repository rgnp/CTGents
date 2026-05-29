# Agent 项目 Makefile
# Windows 兼容：使用 py 启动器，或设置 PYTHON=python

PYTHON := py
PYTEST := $(PYTHON) -m pytest

.PHONY: help install test lint lint-fix run clean check precommit precommit-install coverage

# 默认目标：显示帮助
help:
	@echo "可用目标："
	@echo "  make install        安装依赖"
	@echo "  make test           运行测试"
	@echo "  make lint           代码检查（ruff check）"
	@echo "  make lint-fix       自动修复 lint 问题"
	@echo "  make run            启动 Agent"
	@echo "  make clean          清理临时文件"
	@echo "  make check          项目规范扫描"
	@echo "  make precommit      手动运行 pre-commit 检查"
	@echo "  make precommit-install  安装 pre-commit hooks"
	@echo "  make coverage       测试覆盖率报告"
# 安装依赖
install:
	pip install -r requirements.txt

# 运行测试
test:
	$(PYTEST) -v

# 代码风格检查
lint:
	ruff check src/

# 自动修复
lint-fix:
	ruff check --fix src/
	ruff format src/

# 启动 Agent
run:
	$(PYTHON) run.py

# 清理缓存和临时文件
clean:
	@echo "清理 __pycache__ ..."
	@for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
	@echo "清理 .pytest_cache .mypy_cache .ruff_cache ..."
	@for /d %%d in (.pytest_cache .mypy_cache .ruff_cache) do @if exist "%%d" rd /s /q "%%d" 2>nul
	@echo "清理完成"
# 项目规范检查（需先安装依赖）
check:
	$(PYTHON) -c "from src.tools.lint import check_project; print(check_project())"

# 手动运行 pre-commit（需先 pre-commit install）
precommit-install:
	pre-commit install

precommit:
	pre-commit run --all-files

# 测试覆盖率报告（需先 pip install pytest-cov）
coverage:
	$(PYTEST) --cov --cov-report=term --cov-report=html
	@echo "HTML 报告: htmlcov/index.html"

# 项目规范检查（需先安装依赖）
check:
	$(PYTHON) -c "from src.tools.lint import check_project; print(check_project())"
