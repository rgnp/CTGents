---
name: session-start-checklist
description: 每次会话开始时，先运行 `py src/self_portr
metadata:
  type: strategy
  updated: 2026-06-04T09:42:33Z
---

每次会话开始时，先运行 `py src/self_portrait.py --short` 快速了解项目当前状态（模块数、工具数、测试数、覆盖率、Git 分支和未提交变更）。需要详细认知时运行 `py src/self_portrait.py`（不带参数）。这个脚本比 self.py 工具更准确——它直接 importlib 加载工具模块、AST 解析源码、读取 coverage.json，不会受旧手写描述误导。
