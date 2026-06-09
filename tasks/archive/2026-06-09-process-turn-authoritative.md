# 让交互网权威化：抽 main.process_turn() — 已完成

网的 `_drive_turn` 曾手抄 main 每轮管线="长得像 main 的副本",有 drift 风险
(main 改了副本不跟、测试对旧副本继续绿)。抽出唯一 `process_turn()`,main REPL
与网共用 → drift 闭合。附带:一轮生命周期成了显式可读的函数。

改:
- main.py 新增 `process_turn(ctx, user_input, on_token, on_tool, on_progress, session_id)`
  = 信号→preread→run_conversation→两审计,返回 reply;I/O 留调用方。
- main() 主路径改成 make_display+esc+sid 包 process_turn。
- test_integration_turn `_drive_turn` → 直接调 main.process_turn(同源)。

验证有牙:删管线任一审计步,test_volatile_signals_dont_accumulate 立刻红。
523 绿,ruff 净。

边界(交底):retry/guide 路径仍直接 run_conversation(本就无 preread/审计),
未纳入 process_turn——要不要统一是行为变更,列可选后续。
