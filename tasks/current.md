# 记忆系统三缺口修复

- [x] Step 1: 衰减机制 — params.py 加 decay_rate，memory.py `_score_memory` 乘 age_factor
- [x] Step 2: 检索被动 — `_build_context` 检测活跃任务时追加 recall 微刺激
- [r] Step 3: 索引滚出 — 需主循环改动，暂放
- [ ] Step 4: 测试 + 验证
