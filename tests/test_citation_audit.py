"""引用即取证：audit_citations 抽 path:line 引用、对工具活动判 grounded。

牙窄但准：只罩带行号的代码文件引用；裸文件名不抓（precision 边界）。
grounded 判定故意松（basename∈haystack）→ 宁漏判不误报。
"""
from __future__ import annotations

import json

from src.citation_audit import audit_citations


def _reply(text: str) -> dict:
    """最终交付的 assistant 回复（无 tool_calls）。"""
    return {"role": "assistant", "content": text}


def _read_call(cid: str, path: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "function": {"name": "read_file", "arguments": json.dumps({"path": path})}}]}


def _tool_result(cid: str, name: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": content, "_tool_name": name}


# ── 无引用 / 精度边界 ─────────────────────────────────────────

def test_no_citation_returns_none():
    assert audit_citations([_reply("我修好了那个 bug，测试通过。")]) is None


def test_bare_filename_without_line_not_cited():
    """裸文件名（无行号）不算引用 → 不报（precision 边界，故意放过）。"""
    assert audit_citations([_reply("你可以看看 llm.py 这个文件。")]) is None


def test_empty_log_returns_none():
    assert audit_citations([]) is None


# ── grounded（碰过该文件）→ 不报 ──────────────────────────────

def test_grounded_via_read_file():
    log = [
        _read_call("c1", "src/llm.py"),
        _tool_result("c1", "read_file", "...文件内容..."),
        _reply("如我刚读到的，yield 点在 llm.py:1056。"),
    ]
    assert audit_citations(log) is None


def test_grounded_via_preread():
    """预读把文件内容拼进 user 消息(main 的真实形态，非 tool 消息)→ grounded。

    回归:曾把 preread 误建模成 tool 消息，导致 grounding 漏扫 user 消息 →
    预读过的文件被误报"没读过"。真实路径里预读内容在 user 消息里。
    """
    log = [
        {"role": "user", "content": "[以下文件已预读]\n\n[预读] D:\\project\\src\\config.py\n"
                                    "x = 1\n\n── 用户问题 ──\n修一下 config.py 的 bug"},
        _reply("配置在 config.py:12。"),
    ]
    assert audit_citations(log) is None


def test_grounded_via_user_message():
    """用户直接粘贴/提到的文件名出现在 user 消息里 → grounded（agent 看得见）。"""
    log = [
        {"role": "user", "content": "这是 foo.py 的内容：\ndef f(): ..."},
        _reply("入口是 foo.py:1。"),
    ]
    assert audit_citations(log) is None


def test_grounded_via_write():
    log = [
        _tool_result("w1", "write_file", "已写入: D:\\project\\src\\foo.py（10 字符）"),
        _reply("新逻辑在 foo.py:3。"),
    ]
    assert audit_citations(log) is None


# ── ungrounded（没碰过）→ 报 ──────────────────────────────────

def test_ungrounded_citation_nudges():
    out = audit_citations([_reply("这个在 bar.py:99 处理。")])
    assert out is not None
    assert "bar.py" in out


def test_only_ungrounded_files_listed():
    """只点没取证过的文件，grounded 的不点名。"""
    log = [
        _read_call("c1", "src/llm.py"),
        _tool_result("c1", "read_file", "...内容..."),
        _reply("逻辑在 llm.py:10，错误源在 ghost.py:2。"),
    ]
    out = audit_citations(log)
    assert out is not None
    assert "ghost.py" in out
    assert "llm.py" not in out


# ── 路径形态归一 / 只扫最终回复 ───────────────────────────────

def test_various_path_forms_normalize_to_basename():
    r"""src/x.py / D:\..\x.py / x.py 都归 basename x.py，被同一处 grounding 命中。"""
    log = [
        _tool_result("w1", "write_file", "已写入: D:\\project\\x.py（1 字符）"),
        _reply("见 src/x.py:1、D:\\project\\sub\\x.py:2、x.py:3。"),
    ]
    assert audit_citations(log) is None


def test_only_final_reply_scanned():
    """中间带 tool_calls 的 assistant 消息不算最终回复，其引用不参与判定。"""
    log = [
        {"role": "assistant", "content": "我去看 mid.py:5", "tool_calls": [
            {"id": "c1", "function": {"name": "think", "arguments": "{}"}}]},
        _tool_result("c1", "think", "ok"),
        _reply("处理完毕。"),  # 最终回复无引用
    ]
    assert audit_citations(log) is None


# ── 标识符级取证（agent 谈论自身架构的可检查片） ──────────────

def test_fabricated_identifier_nudges():
    """代码体引用上下文里从没出现过的标识符 → 报（曾凭印象整段描述机制）。"""
    out = audit_citations([_reply("这个由 `_phantom_audit_loop` 兜底处理。")])
    assert out is not None
    assert "_phantom_audit_loop" in out
    assert "新提议" in out, "措辞必须给'新提议的命名'留判断出口"


def test_identifier_grounded_via_tool_result():
    log = [
        _tool_result("c1", "grep_code", "src/main.py:8: def _finalize_session(...)"),
        _reply("收尾走 `_finalize_session`。"),
    ]
    assert audit_citations(log) is None


def test_identifier_grounded_via_system_message():
    """前缀/挂尾的 system 消息（派生机制索引等）是合法取证源 → 不报。"""
    log = [
        {"role": "system", "content": "## 运行时机制\n- `_inject_completion_audit`：收尾取证"},
        _reply("我们有 `_inject_completion_audit` 这层，而且 `completion_audit` 挂尾生效。"),
    ]
    assert audit_citations(log) is None


def test_tool_name_mention_not_flagged():
    """注册工具名（schema 每轮可见）→ 天然 grounded，不报。"""
    assert audit_citations([_reply("我会用 `read_file` 和 `edit_file_lines` 来改。")]) is None


def test_plain_word_and_dunder_not_audited():
    """无下划线的泛词（`recall`）和 dunder（`__init__`）不抓——precision 边界。"""
    assert audit_citations([_reply("可以用 `recall` 搜，`__init__` 里初始化。")]) is None


def test_bare_py_filename_in_backticks_audited():
    """反引号里的 *.py 是刻意代码引用 → 没见过就报（裸文本文件名仍放过）。"""
    out = audit_citations([_reply("逻辑都在 `ghost_module.py` 里。")])
    assert out is not None
    assert "ghost_module.py" in out


def test_identifier_via_tool_call_name_grounded():
    """本轮调过的工具名出现在 tool_calls 里 → grounded。"""
    log = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c9", "function": {"name": "storm_check_x", "arguments": "{}"}}]},
        _tool_result("c9", "storm_check_x", "ok"),
        _reply("刚才 `storm_check_x` 返回 ok。"),
    ]
    assert audit_citations(log) is None


def test_paths_and_identifiers_both_reported():
    """两类各自成段，可同时触发。"""
    out = audit_citations([_reply("在 ghost.py:7，由 `_phantom_hook` 调用。")])
    assert out is not None
    assert "ghost.py" in out and "_phantom_hook" in out
