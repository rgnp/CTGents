# Plan Mode 修成粘性真闸（B）— 已完成

旧:main.py:441 无条件每轮后 set_plan_mode(False) → `/plan` 单轮、自动解锁,
与"批准后才解锁"矛盾。改成粘性:进只读就一直只读,直到用户显式 /plan 批准解锁。

改:
- main.py:440-442 删 set_plan_mode(False),改成"仍在 Plan Mode,/plan 解锁"提醒;
  顺带去掉 main 已无引用的 set_plan_mode import。
- commands.py /plan 进入文案"自动退出"→"持续只读,再次 /plan 批准并解锁"
  (描述行"批准后才解锁"现已为真,保留;"已激活/禁用/已退出"子串保留)。
- test_integration_turn.py 加 test_plan_mode_sticky_across_turn:跑完一轮
  is_plan_mode() 仍 True(钉死管线永不私自清标志)。

唯二碰 plan 标志:/plan toggle(进/出)。535 绿,ruff 净。
