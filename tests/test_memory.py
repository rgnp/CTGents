"""memory.py 核心路径回归：remember / recall / forget / get_context 索引。

此前仅 detect_signal 有测试，核心读写路径裸奔。重点回归：frontmatter 的
闭合分隔符曾用 find("---") 子串匹配，当 description/正文含 '---' 时会错位
→ type 解析为空、body 串入 metadata、索引 desc 丢失。三处解析器共用同一 bug。
"""
from __future__ import annotations

import pytest

from src.tools import memory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """把记忆目录重定向到 tmp，并重置模块级索引缓存，避免污染真实记忆。"""
    monkeypatch.setattr(memory, "MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(memory, "_context_cache", None)
    monkeypatch.setattr(memory, "_context_dirty", True)
    return memory


# ── characterization：锁住正常往返 ──────────────────────────

def test_remember_then_recall(mem):
    mem.execute("remember", {"name": "foo-bar", "content": "用户偏好简短回答", "type": "user"})
    out = mem.execute("recall", {"query": "简短"})
    assert "foo-bar" in out
    assert "[user]" in out


def test_remember_rebuilds_index(mem):
    mem.execute("remember", {"name": "a-note", "content": "某条知识", "type": "knowledge"})
    ctx = mem.get_context()
    assert ctx is not None and "a-note" in ctx


def test_remember_overwrites_same_name(mem):
    mem.execute("remember", {"name": "dup", "content": "旧内容", "type": "user"})
    mem.execute("remember", {"name": "dup", "content": "新内容 fresh_token", "type": "user"})
    out = mem.execute("recall", {"query": "fresh_token"})
    assert "dup" in out
    assert "旧内容" not in out


def test_forget_removes(mem):
    mem.execute("remember", {"name": "gone", "content": "临时 disposable", "type": "user"})
    assert "已忘记" in mem.execute("forget", {"name": "gone"})
    assert "未找到" in mem.execute("recall", {"query": "disposable"})


def test_forget_missing(mem):
    assert "不存在" in mem.execute("forget", {"name": "never-existed"})


def test_recall_miss(mem):
    mem.execute("remember", {"name": "a", "content": "apple", "type": "user"})
    assert "未找到" in mem.execute("recall", {"query": "zzz-no-such-token"})


def test_empty_context(mem):
    assert mem.get_context() is None  # 无任何记忆 → None


# ── 回归：frontmatter 含 '---' 不得破坏解析（修复前失败）──────

def test_dashes_in_content_keep_type(mem):
    """正文首句含 '---' → description 含 '---'，type 仍须正确解析。"""
    mem.execute("remember", {
        "name": "dashy", "content": "--- 重要分隔，记住这个 token_xyz", "type": "strategy",
    })
    out = mem.execute("recall", {"query": "token_xyz"})
    assert "dashy" in out
    assert "[strategy]" in out, f"type 解析错位(应为 strategy): {out!r}"


def test_dashes_in_content_keep_index_desc(mem):
    """摘要含 '---' 时索引 desc 不得丢失。"""
    mem.execute("remember", {
        "name": "dashy2", "content": "--- 边界笔记 marker_q", "type": "user",
    })
    ctx = mem.get_context()
    assert "边界笔记" in ctx, f"索引 desc 因 frontmatter 错位丢失: {ctx!r}"
