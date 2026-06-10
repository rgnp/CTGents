# 规则系统"模型适配"——标签诚实化 + 补两颗真牙 — 已完成

把 AGENTS.md 每条规则按"前缀原则 / 尾部 recency 牙 / 代码牙"三层归位（依据 [[rule-placement-three-layers]]）。
审计前先核实了 hook / ruff / tool_guard 的真实行为，不凭记忆。

改：
- **标签诚实化**：C1 违反即 `代码审查`→`ruff E722`（select 含 E，本就被拦，标签过期）；
  C4/C5 `grep`→`审查`（hook 实际无 grep 在跑，且 `f"`/`...` 粗匹配高误报=假阳性陷阱，
  `...` 在 Protocol 合法会误伤自身）；C2 违反即→`pre-commit 密钥格式扫描`。
- **补真牙**：`tool_guard` 加 `run_command` 分支——P1 拦 `git add -A`/`.`、P2 拦 force-push
  到 main/master（确定正则、零判断；P1 还正好对抗模型 `git add -A` 强默认）；
  `pre-commit` 加 C2 密钥格式扫描（只匹配 `sk-`/`AKIA`/`ghp_` 高熵格式，不匹配裸词）。
- AGENTS.md P 表下加注 P1/P2 已由 tool_guard 拦。

验证有牙：删 run_command 分支，test_tool_guard 的 P1/P2 用例立刻红。
repo 已 grep 确认零密钥格式匹配，新 C2 闸不误锁。529 绿，ruff 净。

## 完成总结
- 计划 5 步 → 实际 5 步（0 回退）
- 教训：审计"规则在不在对的层"前必须核实它**现在到底有没有牙**——C1 标"审查"实为 ruff 牙、
  C2/C4/C5 标 grep 实则没 grep，全是凭记忆会判错的点。挂"画上去的牙"比没牙糟（假装管住了）。
- 缓：P3/P4（带"除非用户要求"=需软牙）、tail 候选（不盲从/最小变更，等真实证据）。
