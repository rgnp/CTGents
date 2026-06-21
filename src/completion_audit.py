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
# run_command 仅在非零退出码时前置此串（退 0 不带）。仅"无此前缀"还不够判绿——见下。
_EXIT_PREFIX = "退出码:"
# write_file / edit_file_lines 成功结果前缀（行内含被改文件路径）。
_WRITE_OK = "已写入:"
_EDIT_OK = "已编辑:"
# git_commit 成功前缀（能提交 ⟹ pre-commit 已跑全量绿）。
_COMMIT_OK = "✅ 提交成功"
# 绿测的输出信号：命令串含 "pytest" 不足以判绿——`echo pytest` / `pytest --version` /
# `pytest --collect-only`（只收集不执行）都含子串却没真跑用例，退 0 也无此串。要求
# 结果里出现真实通过统计才认绿，否则这类命令可伪造绿测、压掉 staleness 提示。
_GREEN_PYTEST_SIGNAL = "passed"

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
        if "pytest" not in cmd or content.startswith(_EXIT_PREFIX):
            return False
        # 退 0 还不够：要有真实通过统计，挡 echo pytest / --version / --collect-only。
        return _GREEN_PYTEST_SIGNAL in content.lower()
    return False


def _is_py_edit(msg: dict) -> bool:
    """该 tool 结果是否一次成功的 .py 改盘（失败/非 .py 不算）。"""
    name = msg.get("_tool_name")
    first = (msg.get("content") or "").split("\n", 1)[0]
    if name == "write_file":
        return first.startswith(_WRITE_OK) and ".py" in first
    # edit_file_lines 与 replace_in_file 都用 "已编辑:" 前缀（后者字符串匹配式编辑，去行号漂移）。
    if name in ("edit_file_lines", "replace_in_file"):
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


_WRITE_WITHOUT_READ_NUDGE = (
    "⚠️ 取证自检：本轮你修改了文件（write_file / edit_file_lines）"
    "但本轮没有先调用 read_file 读过目标文件。行号/内容可能已漂移，"
    "下次改文件前先读一遍——不读就改是最高频的原地踏步来源。"
)


def _tool_names_this_turn(log: list[dict]) -> set[str]:
    """收集本轮所有 tool_calls 的工具名（从最后一条 user 消息起）。
    没有 user 消息则返回空集合——没有轮次边界就无从审计。
    """
    if not any(m.get("role") == "user" for m in log):
        return set()
    names: set[str] = set()
    for msg in reversed(log):
        role = msg.get("role", "")
        for tc in msg.get("tool_calls") or []:
            name = tc.get("function", {}).get("name", "")
            if name:
                names.add(name)
        if role == "user":
            break
    return names


def audit_read_before_write(log: list[dict]) -> str | None:
    """本轮调了 write_file / edit_file_lines 但没调 read_file → 返回提示。"""
    names = _tool_names_this_turn(log)
    writes = {"write_file", "edit_file_lines"}
    if writes & names and "read_file" not in names:
        return _WRITE_WITHOUT_READ_NUDGE
    return None


# ── 记忆-行为闭环：内部记忆/知识库咨询 ──
_MEMORY_CONSULT = {"recall", "rag_search", "rag_query"}      # 查"我以前记过/研究过吗"
_SUBSTANTIVE_WORK = {                                         # 本该先查再动手的实质活
    "write_file", "replace_in_file", "edit_file_lines", "delete_file",
    "move_file", "copy_file", "search_web", "learn", "scan_papers",
}
_NO_MEMORY_CONSULT_NUDGE = (
    "⚠️ 记忆未咨询：这轮干了实质活（改文件 / 外部调研），但整个会话还没 recall / "
    "rag_search 过内部记忆和知识库。你有「任务开始先查（我之前研究过 / 踩过坑吗）」的规则——"
    "下次动手或外部调研前先查一次内部记忆，别每次从零开始、重复造轮子。"
)


# ── 质量自检 ──
_QUALITY_CHECK_MARKER = "[x] 质量自检"          # current.md 中质量步骤已完成
_NO_QUALITY_CHECK_NUDGE = (
    "⚠️ 质量未自检：这轮干了实质活（改文件 / 外部调研 / 研究），但整个会话还没做质量自检。"
    "你有「三问质量方法论」规则——交付前先问自己：真的做好了吗？"
    "若在复杂任务中，给 current.md 加 `- [ ] 质量自检` 步骤，做完后标 `[x]` 再调 task_done。"
)


def audit_quality_check(log: list[dict]) -> str | None:
    """本轮干了实质活 + 整个会话没做过质量自检 → 提示先自检。

    质量自检 = 在 current.md 中完成 `[x] 质量自检` 步骤（log 中出现该标记即为已做）。
    """
    if not (_SUBSTANTIVE_WORK & _tool_names_this_turn(log)):
        return None
    for msg in log:
        content = msg.get("content") or ""
        if _QUALITY_CHECK_MARKER in content:
            return None
    return _NO_QUALITY_CHECK_NUDGE


def audit_memory_consult(log: list[dict]) -> str | None:
    """本轮干实质活 + 整个会话没 recall/rag_search 过 → 提示先查内部记忆。

    断点取证（2026-06-21）：「任务开始先 rag_search/recall」是 strategy 规则、每会话全文
    注入进前缀，但前缀=最弱影响层（5a96363）——实测最近 15 会话 771 次工具调用里 rag_search
    0 次、recall ~1/会话。规则记了、注入了、就是不执行。唯一被验证能改行为的是 append-only
    recency 审计通道，故在此戳一下让 agent 自己去查（不替它检索，区别于已删的 auto_recall）。
    """
    if not (_SUBSTANTIVE_WORK & _tool_names_this_turn(log)):
        return None
    for m in log:  # 整个会话任意处咨询过内部记忆 → 不再唠叨
        for tc in m.get("tool_calls") or []:
            if tc.get("function", {}).get("name", "") in _MEMORY_CONSULT:
                return None
    return _NO_MEMORY_CONSULT_NUDGE
