# 当前长任务

## 目标
两项缓存/成本优化：stormBreaker 防死亡螺旋 + 压缩阈值提升

## 步骤

- [x] Step 1: params.py — COMPACT_THRESHOLD 0.65→0.80, KEEP_RATIO 0.40→0.50 ✓
- [x] Step 2: llm.py — stormBreaker 同轮死亡螺旋检测 ✓
  - 签名：(tool_name, error) 拼接，key on error not args
  - 连续 3 次同一签名 → 尾部注入 [loop guard] 指令
  - 任意成功或不同错误 → 重置
- [x] Step 3: ruff + pytest 关键模块全绿 ✓
