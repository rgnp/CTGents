# 给"复读机"装牙：思考提醒挂尾部 — 已完成

症结：问开放问题时 agent 复读检索结果 + 甩菜单，不给判断（"文件复读机"）。多轮实验
定位：检索命中短路思考；且 AGENTS.md 前缀里的同义 bullet 翻不动这个默认（3 次全退回
复读+菜单+甩锅），尾部同措辞注入则稳定翻成"给判断"、事实问不被吹长。结论=前缀放原则、
尾部放牙（与 mem_signal / 两审计同模式）。

改：
- AGENTS.md「发散拓展」改写为「给判断，别只检索」（原则，前缀半边）。
- main.py 加 `_THINKING_NUDGE` + `_inject_thinking_stance(ctx)`，process_turn 在
  run_conversation 前注入（紧跟 _inject_memory_signal）；常驻不设门，strip-then-append。
- 交互网加 test_thinking_stance_rides_tail_once（每轮恒一条、不累积、在尾）。

验证有牙：缝测断言尾部恒一条 _thinking_stance，删注入即 0、立刻红。
524 绿，ruff 净。

## 完成总结
- 计划 4 步 → 实际 4 步（0 回退）
- 教训：前缀 prompt 劝不动根深蒂固的默认行为；行为牙必须挂 log 尾靠 recency。
  AGENTS.md 纯提示条 = 文档 + 轻偏置，不是控制台。
