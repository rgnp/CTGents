# 收尾取证自检（④可信 · 治"谎报完成"B）— 已完成

落地:`src/completion_audit.py`(纯函数 `audit_completion`)+ `src/main.py`
主路径返回后注入 volatile 尾 + AGENTS.md 验证节加"完成类断言要落地" +
`tests/test_completion_audit.py`(11 单测 + 4 契约不变量)。518 全绿,ruff 净。

## 判定（纯结构,不解析散文）
- green = `run_command` 含 pytest 且结果无 `退出码:` 前缀(退0=全过;id→命令关联)
  ∪ `git_commit` 结果以 `✅ 提交成功` 开头(pre-commit 已跑全量绿)
- edit = `write_file`→`已写入: ….py` / `edit_file_lines`→`已编辑: ….py`(自包含)
- stale = 有 edit 且(无 green 或 last_edit > last_green) → 走全 log,自解决

## C16 要点
审计跨模块读 file/git/exec 的成功 marker(天然脆)→ TestOutputContracts
用真实工具把 `退出码:`/`已写入:`/`已编辑:` 钉死,谁改输出格式立刻报警。

## 已知限制(v1 交底)
- 迟一轮(流式 + 零额外 LLM 调用的取舍)
- 不追测试范围(单文件 pytest 也算 green)
- git_commit 成功的契约靠源码常量+注释,不做集成测试(需真 repo+绿树)
- 只接主路径;retry/guide 路径靠"全 log 扫描"由下一次主路径兜
