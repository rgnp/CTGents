"""收尾取证自检：扫描动作日志，判断"完成类断言"是否被最新改动作废。

治 ④可信 的"谎报完成"：agent 说"测试通过/搞定了"，而最后一次代码改动晚于
最后一次绿测时——这个结论已经过期。这里只供事实（machine-checkable），
"算不算完成"是判断题，交给 agent。形态：探测 + 提示，不强制。

本模块跨模块读其它工具的输出串（file/git/exec 的成功 marker），天然脆——
test_completion_audit.py 用契约不变量把这些 marker 钉死（C16：新接线即新不变量）。
"""
from __future__ import annotations

import json

# ── 审计依赖的输出契约（structural，非旋钮 → 留本模块，不进 params.py）──
# run_command 仅在非零退出码时前置此串；结果无此前缀 ⟹ 退 0 ⟹ pytest 全过。
_EXIT_PREFIX = "退出码:"
# write_file / edit_file_lines 成功结果前缀（行内含被改文件路径）。
_WRITE_OK = "已写入:"
_EDIT_OK = "已编辑:"
# git_commit 成功前缀（能提交 ⟹ pre-commit 已跑全量绿）。
_COMMIT_OK = "✅ 提交成功"

_NUDGE = (
    "⚠️ 取证自检：本会话最后一次代码改动晚于最后一次绿测（或全程没跑过测试）。"
    "若你刚向用户表达了'完成/测试通过'，那是未经验证的——补跑 pytest 或更正措辞；"
    "若只是中途进展（WIP），忽略本提示。"
)


def _call_commands(log: list[dict]) -> dict[str, str]:
    """从 assistant 消息收 tool_call_id -> run_command 的命令串（结果不回显命令）。"""
    cmds: dict[str, str] = {}
    for msg in log:
        for tc in msg.get("tool_calls") or []:
            if tc.get("function", {}).get("name") != "run_command":
                continue
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            cmd = args.get("command")
            if cmd:
                cmds[tc.get("id")] = cmd
    return cmds


def _is_green(msg: dict, commands: dict[str, str]) -> bool:
    """该 tool 结果是否一次绿测 / 绿提交。"""
    name = msg.get("_tool_name")
    content = msg.get("content") or ""
    if name == "git_commit":
        return content.startswith(_COMMIT_OK)
    if name == "run_command":
        cmd = commands.get(msg.get("tool_call_id"), "")
        return "pytest" in cmd and not content.startswith(_EXIT_PREFIX)
    return False


def _is_py_edit(msg: dict) -> bool:
    """该 tool 结果是否一次成功的 .py 改盘（失败/非 .py 不算）。"""
    name = msg.get("_tool_name")
    first = (msg.get("content") or "").split("\n", 1)[0]
    if name == "write_file":
        return first.startswith(_WRITE_OK) and ".py" in first
    if name == "edit_file_lines":
        return first.startswith(_EDIT_OK) and ".py" in first
    return False


def audit_completion(log: list[dict]) -> str | None:
    """最后一次 .py 改动晚于最后一次绿测（或全程无绿测）→ 返回提示，否则 None。

    走全 log 扫描而非仅本轮 ⟹ "未验证"提示会持续到补跑为止，自解决。
    """
    commands = _call_commands(log)
    last_edit = last_green = -1
    for i, msg in enumerate(log):
        if msg.get("role") != "tool":
            continue
        if _is_py_edit(msg):
            last_edit = i
        elif _is_green(msg, commands):
            last_green = i
    if last_edit >= 0 and last_edit > last_green:
        return _NUDGE
    return None
