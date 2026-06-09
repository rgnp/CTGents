# 删除 Plan Mode 长度自动触发 — 已完成

`_should_auto_plan` = `len(input)>=300` 是把"该不该先只读分析"这个判断题焊成
硬规则:长度⊥复杂度(短而复杂放过/长而琐碎误锁),且 main 的 preread 把文件内容
塞进 input → 一提文件几乎必触发(量的是膨胀后的输入,不是用户原话)。同一路在删的
反模式。删除自动触发,Plan Mode 保留显式 `/plan`(commands.py:464),不孤儿。

改:llm.py(去 import 的 is/set_plan_mode、删 _AUTO_PLAN_MIN_CHARS/_should_auto_plan/
auto-plan 块/`if auto_plan: set_plan_mode(False)`)、params.py(删死旋钮
auto_plan_min_chars)、test_params.py(删 2 条断言)。ruff 净,528 绿。
