---
name: dev-workflow-docs-must-sync
description: 任何功能开发完成后必须同步更新以下文档（写代码只是完成了一半
metadata:
  type: strategy
  updated: 2026-05-29T15:16:42Z
---

任何功能开发完成后必须同步更新以下文档（写代码只是完成了一半）：
1) AGENTS.md — 新增/修改工具模块、命令、安全等级等必须同步
2) docs/architecture.md — 核心模块/数据流变化必须同步
3) docs/development.md — 开发流程/工具注册变化必须同步
4) README.md — 功能列表/技术栈/命令变化必须同步
5) docs/changelog.md — 每次提交必须记录变更
6) docs/features.md — 每个版本的功能点必须记录
7) docs/roadmap.md — 版本状态变化必须更新

执行机制：
- docs_sync_check 工具（src/tools/lint.py）：硬编码 _DOC_SYNC_MAP 映射表，自动检查违规
- pre-commit 钩子：每次 git commit 自动运行 docs_sync_check
- CI job：每次 push 自动运行 docs_sync_check，违规则阻止合并
- Makefile: make docs-sync 手动检查, make preflight 一站式检查
- AGENTS.md 中明确列出了完整映射表和提交流程
