# 当前长任务

## 目标
同轮工具结果去重 — 相同 (tool, args) 只执行一次，复用缓存

## 步骤

- [o] Step 1: llm.py — 导入 DEDUP_BLACKLIST + 去重缓存逻辑
- [ ] Step 2: ruff + pytest 相关模块，提交
