"""命令系统端到端测试。"""

import pytest
from src.commands import CmdResult, dispatch, _cmd_context


class TestContextCommand:
    """/context 命令端到端测试 — 确保不崩溃。"""

    def test_context_no_messages(self):
        """空消息列表不应崩溃。"""
        msgs: list[dict] = []
        r = CmdResult()
        _cmd_context(r, msgs, [], "")
        assert r.message
        assert "对话上下文" in r.message

    def test_context_with_system_messages(self):
        """有系统消息时不应崩溃。"""
        msgs = [
            {"role": "system", "content": "当前环境:\n- 工作目录: /tmp\n- 当前时间: 2026-01-01"},
            {"role": "system", "content": "安全模式: MANUAL"},
            {"role": "user", "content": "hello"},
        ]
        r = CmdResult()
        _cmd_context(r, msgs, [], "")
        assert r.message
        assert "前缀哈希" in r.message

    def test_context_with_compacted(self):
        """有压缩消息时不应崩溃。"""
        msgs = [
            {"role": "system", "content": "当前环境: test"},
            {"role": "system", "content": "对话摘要: ⏪ 已压缩"},
            {"role": "user", "content": "hello"},
        ]
        r = CmdResult()
        _cmd_context(r, msgs, [], "")
        assert r.message

    def test_context_via_dispatch(self):
        """通过 dispatch 调用 /context。"""
        msgs = [{"role": "user", "content": "hello"}]
        r = dispatch("/context", msgs, "")
        assert r.message
        assert "前缀缓存" in r.message


class TestHelpCommand:
    """/help 命令测试。"""

    def test_help_does_not_crash(self):
        msgs: list[dict] = []
        r = dispatch("/help", msgs, "")
        assert r.message
        assert "指令列表" in r.message


