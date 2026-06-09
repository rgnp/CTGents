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
    """用户提到的文件被预读 → tool 结果含路径 → grounded。"""
    log = [
        _tool_result("p1", "read_file", "[预读] D:\\project\\src\\config.py\n...内容..."),
        _reply("配置在 config.py:12。"),
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
