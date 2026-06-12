# 自进化实验：解锁 tier_3

## 目标
将全局覆盖率从 64% 提升到 75%，解锁 tier_3（guard.py / main.py / tools/__init__.py）。

## 步骤

- [x] Step 1: 摸底 — 64% → 需要 +928 行
- [x] Step 2: 选目标 — project.py + rag.py + lint.py
- [x] Step 3: project.py — 65% → 88%（+64 行覆盖）
- [o] Step 4: rag.py — 扩展 chunk/增量/研究索引测试（目标 +80+ 行）
- [ ] Step 5: 验收 — 跑全量测覆盖率，确认 75%+
- [ ] Step 6: 改核心 — 选 tier_3 模块做改进
- [ ] Step 7: commit + archive
